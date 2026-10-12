"""Main audio processing pipeline for BibleLive."""

import logging
import threading
import time
from queue import Empty, Full, Queue

from core.bible_lookup import BibleLookup
from core.verse_parser import ReferenceParser, StreamingParser

from .capture import AudioCapture
from .config import AudioPipelineConfig
from .control import LanguageControlServer
from .metrics import PipelineMetrics
from .sender import ReferenceSender
from .stt import SUPPORTED_LANGUAGES, SpeechToText
from .vad import VoiceActivityDetector

logger = logging.getLogger(__name__)


class BibleLiveAudioProcessor:
    """Main audio processing pipeline for BibleLive with reliability improvements"""

    def __init__(
        self,
        bible_db_path: str = "bible_data/biblelive.sqlite",
        server_url: str = "http://localhost:8000",
        config: AudioPipelineConfig | None = None,
    ):
        # Configuration
        self.config = config or AudioPipelineConfig()
        self.bible_db_path = bible_db_path
        self.server_url = server_url

        # Initialize components with bounded queues
        self.audio_capture = AudioCapture(queue_maxsize=self.config.audio_queue_maxsize)
        self.vad = VoiceActivityDetector(aggressiveness=self.config.vad_aggressiveness)
        self.stt = SpeechToText(
            model_size=self.config.whisper_model,
            device=self.config.whisper_device,
            language=self.config.language,
            compute_type=self.config.whisper_compute_type,
        )

        # VAD queue for speech segments (bounded). Each item is
        # ``(language, frames)``: the language the utterance was captured in, so
        # a switch does not retroactively re-tag segments already queued.
        self.vad_queue: Queue[tuple[str, list[bytes]]] = Queue(
            maxsize=self.config.vad_queue_maxsize
        )

        # State tracking
        self.is_running = False
        self.speech_frames: list[bytes] = []
        self.silent_frames = 0
        self.frame_duration_ms = 30  # WebRTC VAD frame size (10/20/30ms)
        self._frame_duration_s = self.frame_duration_ms / 1000
        self.max_silent_frames = 50  # ~1.5 seconds of silence to end utterance
        self.min_speech_frames = 10  # Minimum frames to consider as speech (~300ms)

        # Metrics
        self.metrics = PipelineMetrics()
        self.last_metrics_log = time.time()

        # Reference delivery (dedup + retry-with-backoff)
        self.sender = ReferenceSender(server_url, self.config, self.metrics)

        # Thread-local storage for BibleLookup + per-language parsers (owned by
        # the transcription thread).
        self._local = threading.local()

        # Runtime language switching. The control thread records a request in
        # ``_pending_language``; the VAD thread applies it to its own capture
        # state; the transcription thread picks the parser from each utterance's
        # tag. ``_capture_language`` is only touched by the VAD thread.
        self._capture_language: str = self.config.language
        self._pending_language: str | None = None
        self._pending_language_lock = threading.Lock()
        self.control_server: LanguageControlServer | None = None

        # Worker threads
        self.vad_thread: threading.Thread | None = None
        self.transcription_thread: threading.Thread | None = None

    def start(self):
        """Start the audio processing pipeline.

        Raises:
            RuntimeError: if the pipeline is already running. The guard is
                outside the rollback ``try`` so a rejected second start cannot
                unwind the live run (stop capture, join its threads, or leak
                its bound control socket).
        """
        if self.is_running:
            raise RuntimeError("Audio processor is already running")

        logger.info("Starting BibleLive Audio Pipeline...")

        try:
            # Bind the control server before anything else so a busy port fails
            # startup clearly instead of after capture is already running.
            if self.config.control_enabled:
                self.control_server = LanguageControlServer(
                    get_language=lambda: self.current_language,
                    set_language=self.set_language,
                    host=self.config.control_host,
                    port=self.config.control_port,
                    token=self.config.control_token,
                )
                self.control_server.start()

            self.is_running = True

            # Start audio capture
            self.audio_capture.start_capture()

            # Start VAD processing thread
            vad_thread = threading.Thread(target=self._vad_loop, daemon=True, name="vad-processor")
            self.vad_thread = vad_thread
            vad_thread.start()

            # Start transcription thread
            transcription_thread = threading.Thread(
                target=self._transcription_loop, daemon=True, name="transcription-processor"
            )
            self.transcription_thread = transcription_thread
            transcription_thread.start()
        except BaseException:
            # A partially started pipeline must not leave the control port bound,
            # capture running, or is_running stuck True for direct callers.
            self._rollback_start()
            raise

        logger.info("Audio pipeline started. Listening for Bible references...")

    def _rollback_start(self) -> None:
        """Unwind a failed :meth:`start`, preserving the original exception."""
        self.is_running = False
        try:
            self.audio_capture.stop_capture()
        except Exception as e:
            logger.warning(f"Error stopping audio capture during startup rollback: {e}")
        if self.control_server is not None:
            try:
                self.control_server.stop()
            except Exception as e:
                logger.warning(f"Error stopping control server during startup rollback: {e}")
            self.control_server = None
        for thread in (self.vad_thread, self.transcription_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)

    def stop(self):
        """Stop the audio processing pipeline"""
        logger.info("Stopping audio pipeline...")
        self.is_running = False

        # Stop audio capture
        self.audio_capture.stop_capture()

        # Stop the runtime language-control server
        if self.control_server is not None:
            self.control_server.stop()
            self.control_server = None

        # Wait for worker threads
        if self.vad_thread and self.vad_thread.is_alive():
            self.vad_thread.join(timeout=2.0)
        if self.transcription_thread and self.transcription_thread.is_alive():
            self.transcription_thread.join(timeout=2.0)

        # Close any thread-local BibleLookup instances
        if hasattr(self._local, "bible_lookup") and self._local.bible_lookup:
            try:
                self._local.bible_lookup.close()
            except Exception as e:
                logger.warning(f"Error closing Bible lookup: {e}")

        # Log final metrics
        self.metrics.log_summary()
        logger.info("Audio pipeline stopped")

    @property
    def current_language(self) -> str:
        """The language the pipeline is currently transcribing in."""
        return self.stt.language

    def set_language(self, language: str) -> None:
        """Switch the spoken language at runtime (safe from the control thread).

        - the STT language/initial prompt are swapped atomically, so the next
          transcription uses the new language immediately;
        - the switch is posted to the VAD thread, which discards any partial
          utterance captured in the old language so it is not transcribed as
          the new one;
        - utterances already queued keep the tag they were captured with and
          are parsed with the matching parser.

        Raises:
            ValueError: if ``language`` is not in ``SUPPORTED_LANGUAGES``.
        """
        self.stt.set_language(language)
        with self._pending_language_lock:
            self._pending_language = language
        logger.info("Language switch requested: %s", language)

    def _consume_pending_language_switch(self) -> None:
        """Apply a pending language switch (VAD thread only).

        The VAD thread owns ``speech_frames``/``silent_frames``, so only it may
        discard a partial utterance; the control thread merely records the
        request. Queued utterances are left alone.
        """
        with self._pending_language_lock:
            pending = self._pending_language
            self._pending_language = None

        if pending is None or pending == self._capture_language:
            return

        if self.speech_frames:
            logger.info(
                "Language switched to '%s': discarding partial utterance (%d frames)",
                pending,
                len(self.speech_frames),
            )
        self.speech_frames = []
        self.silent_frames = 0
        self._capture_language = pending

    def _vad_loop(self):
        """VAD processing loop - detects speech segments"""
        frame_duration_ms = self.frame_duration_ms

        while self.is_running:
            # Apply any runtime language switch before capturing more audio.
            self._consume_pending_language_switch()

            # Get audio frame
            frame = self.audio_capture.get_audio_frame(frame_duration_ms)
            if frame is None:
                time.sleep(0.01)
                continue

            self.metrics.audio_chunks_captured += 1

            # Check for voice activity
            is_speech = self.vad.is_speech(frame)

            if is_speech:
                # Speech detected
                if not self.speech_frames:
                    logger.info("Voice activity detected, capturing utterance...")
                self.silent_frames = 0
                self.speech_frames.append(frame)
            else:
                # Silence detected
                if self.speech_frames:
                    # We were in speech, now silent - check if utterance ended
                    self.silent_frames += 1

                    if self.silent_frames > self.max_silent_frames:
                        # End of utterance detected - send to transcription queue
                        self._queue_speech_segment()
                        self.speech_frames = []
                        self.silent_frames = 0
                else:
                    # Still in silence
                    self.silent_frames = 0

            # Update queue depth metrics
            self.metrics.audio_queue_depth = self.audio_capture.audio_queue.qsize()
            self.metrics.vad_queue_depth = self.vad_queue.qsize()

            # Periodic metrics logging
            if time.time() - self.last_metrics_log > self.config.metrics_log_interval:
                self.metrics.log_summary()
                self.last_metrics_log = time.time()

    def _queue_speech_segment(self) -> None:
        """Queue the current speech frames tagged with the capture language."""
        utterance_seconds = self._frame_duration_s * len(self.speech_frames)
        if len(self.speech_frames) >= self.min_speech_frames:
            try:
                self.vad_queue.put((self._capture_language, self.speech_frames.copy()), timeout=0.1)
                self.metrics.vad_segments_detected += 1
                logger.info(
                    f"Utterance ended: {utterance_seconds:.2f}s of speech "
                    f"({len(self.speech_frames)} frames, {self._capture_language}) "
                    f"queued for transcription"
                )
            except Full:
                logger.warning("VAD queue full, dropping speech segment (slow transcription)")
        else:
            logger.debug(
                f"Ignoring {utterance_seconds:.2f}s blip "
                f"({len(self.speech_frames)} frames < {self.min_speech_frames} minimum)"
            )

    def _transcription_loop(self):
        """Transcription processing loop - transcribes speech segments"""
        # Initialize thread-local Bible lookup and one parser per language
        self._init_thread_local_parsers()

        while self.is_running:
            try:
                # Get speech segment (with the language it was captured in)
                language, speech_frames = self.vad_queue.get(timeout=0.5)
            except Empty:
                continue

            # Process the utterance
            self._process_utterance(language, speech_frames)

    def _init_thread_local_parsers(self):
        """Initialize the thread-local Bible lookup and one parser per language.

        Parsers live in the transcription thread's thread-local storage: the
        control thread cannot rebuild them (its ``threading.local()`` namespace
        is a different one) and must not, so it only ever sets a flag.
        """
        if not hasattr(self._local, "bible_lookup"):
            try:
                self._local.bible_lookup = BibleLookup(self.bible_db_path, readonly=True)
                logger.debug(f"Thread {threading.current_thread().name}: Initialized Bible lookup")
            except Exception as e:
                logger.error(
                    f"Thread {threading.current_thread().name}: Failed to initialize Bible lookup: {e}"
                )
                self._local.bible_lookup = None

        lookup = self._local.bible_lookup
        validator = lookup.get_validator_hook() if lookup and lookup.conn else None

        # English and Lithuanian parsing stay separate implementations; the
        # language selects which tables a parser is built with.
        parsers: dict[str, StreamingParser] = {}
        for language in SUPPORTED_LANGUAGES:
            try:
                parser = ReferenceParser(validator=validator, language=language)
                parsers[language] = StreamingParser(parser, language=language)
            except Exception as e:
                logger.error(f"Failed to initialize '{language}' parser: {e}")

        self._local.streaming_parsers = parsers
        self._local.last_parser_language = None
        logger.debug(
            f"Thread {threading.current_thread().name}: Initialized parsers for "
            f"{', '.join(parsers) or 'no languages'}"
        )

    def _select_parser(self, language: str) -> StreamingParser | None:
        """Select the parser for ``language``, resetting context on a switch."""
        parsers: dict[str, StreamingParser] = getattr(self._local, "streaming_parsers", None) or {}
        parser = parsers.get(language)
        if parser is None:
            logger.warning("No streaming parser available for language '%s'", language)
            return None

        if language != getattr(self._local, "last_parser_language", None):
            # Previous-language context (carry tokens + inferred chapter/verse)
            # must not leak across the switch.
            parser.reset()
            self._local.last_parser_language = language
            logger.info("Selected '%s' parser (context reset)", language)

        return parser

    def _process_utterance(self, language: str, speech_frames: list[bytes]):
        """Process a completed utterance captured in ``language``"""
        audio_seconds = self._frame_duration_s * len(speech_frames)
        logger.info(
            f"Processing utterance ({len(speech_frames)} frames, {audio_seconds:.2f}s audio, "
            f"language={language})..."
        )

        # Add all frames to STT buffer
        for frame in speech_frames:
            self.stt.add_audio_frame(frame)

        # Transcribe with the language this utterance was captured in, so a
        # runtime switch cannot re-language a segment that was already queued.
        self.metrics.transcription_attempts += 1
        result = self.stt.transcribe_utterance(language)
        transcript, duration_ms = result.text, result.duration_ms

        if transcript is None:
            self.metrics.transcription_failures += 1
            logger.debug("No transcription produced")
            return

        self.metrics.transcription_successes += 1
        self.metrics.record_transcription_duration(duration_ms)
        logger.info(f"Transcription ({duration_ms:.0f}ms): '{transcript}'")

        # Process with the parser for the utterance's capture language
        parser = self._select_parser(language)
        if parser is None:
            logger.warning("Streaming parser not initialized, cannot detect references")
            return

        try:
            # Wallclock-stamped so a book/chapter context older than the
            # TTL is dropped instead of attaching to stray numbers.
            references = parser.feed(transcript, time.time())
        except Exception as e:
            logger.error(f"Error parsing references: {e}", exc_info=True)
            return

        if not references:
            logger.debug("No Bible references detected in transcription")
            return

        # One utterance can legitimately mention the same book/chapter more than
        # once ("Romans 8:28 ... Romans 8:28"), but a transcription that yields the
        # same reference dozens of times is a decoder artifact. Collapse them here
        # so logs and metrics describe what was actually heard.
        unique: list = []
        seen: set[tuple] = set()
        for ref in references:
            key = (ref.book, ref.chapter, ref.verse_start, ref.verse_end)
            if key in seen:
                self.metrics.references_deduplicated += 1
                continue
            seen.add(key)
            unique.append(ref)

        if len(unique) < len(references):
            logger.warning(
                f"Collapsed {len(references) - len(unique)} duplicate reference(s) "
                f"within this utterance"
            )

        # A shaky decode (probably silence, unsure tokens, or a trimmed loop)
        # is exactly where prompt-echo hallucinations come from. Its references
        # still reach the operator, but as "low" so auto-approve won't show them.
        if result.low_confidence_reason is not None:
            self.metrics.references_low_confidence += len(unique)
            logger.warning(
                f"Low-confidence transcription ({result.low_confidence_reason}): "
                f"marking {len(unique)} reference(s) as low confidence"
            )
            for ref in unique:
                ref.confidence = "low"

        self.metrics.references_detected += len(unique)
        logger.info(f"Found {len(unique)} Bible reference(s):")
        for ref in unique:
            logger.info(
                f"  - {ref} [{ref.confidence}]" + (" (from context)" if ref.inferred else "")
            )

            # Send to BibleLive server with deduplication
            self.sender.send(ref)
