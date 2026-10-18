"""Main audio processing pipeline for BibleLive."""

import logging
import os
import threading
import time
from operator import mul
from pathlib import Path
from queue import Empty, Full, Queue

from core.bible_lookup import BibleLookup
from core.verse_parser import Reference, ReferenceParser, StreamingParser
from core.verse_text import VerseTextIndex

from .capture import AudioCapture
from .config import AudioPipelineConfig
from .control import LanguageControlServer
from .ensemble import merge_agreement
from .heartbeat import HeartbeatSender
from .metrics import PipelineMetrics
from .segmenter import FRAME_DURATION_MS, Segmenter, SegmentEvent
from .sender import ReferenceSender
from .session_log import SessionLog
from .spelling_maps import MappedStreamingParser, spelling_map
from .stt import SUPPORTED_LANGUAGES, SpeechToText, Transcription
from .stt_gguf import (
    GGUF_LANGUAGES,
    MultiModelSpeechToText,
    create_gguf_speech_to_text,
    engine_models,
)
from .transcript_sender import TranscriptSender
from .vad import create_vad

logger = logging.getLogger(__name__)

# Seconds without a single audio frame before the operator is told capture has
# stalled. Frames arrive every 30 ms, so this is far beyond any normal hiccup.
AUDIO_STALL_S = 5.0

# Seconds with no sound from the microphone before the operator is told it is
# silent. A dead input keeps delivering frames, so the stall check never fires
# for it. Incident (Windows, 2026-10): the launcher auto-picked "Microphone
# (Realtek(R) Audio)", which delivered about -80 dBFS RMS for 100 s; nothing
# warned and no references were detected.
SILENT_MIC_S = 30.0
# A frame counts as sound when its RMS is above this level (dBFS, 16-bit full
# scale). Room noise sits at -60 to -45 dBFS and the dead input was at -80, so
# -70 is the midpoint of that gap: 10 dB clear of both, and a quiet room between
# speech does not trip the warning.
SOUND_LEVEL_DBFS = -70.0
_SOUND_MEAN_SQUARE = (32768 * 10 ** (SOUND_LEVEL_DBFS / 20)) ** 2


def frame_is_loud(frame: bytes) -> bool:
    """Whether a 16-bit mono PCM frame's RMS is above SOUND_LEVEL_DBFS.

    Compares mean squares, so there is no square root or logarithm per frame.
    Native byte order, which is little-endian on every platform this runs on.
    """
    samples = memoryview(frame).cast("h")
    energy: int = sum(map(mul, samples, samples))
    return energy > _SOUND_MEAN_SQUARE * len(samples)


class BibleLiveAudioProcessor:
    """Main audio processing pipeline for BibleLive with reliability improvements"""

    def __init__(
        self,
        bible_db_path: str = "bible_data/biblelive.sqlite",
        server_url: str = "http://localhost:8000",
        config: AudioPipelineConfig | None = None,
        verse_text_db_path: str | None = None,
    ):
        # Configuration
        self.config = config or AudioPipelineConfig()
        self.bible_db_path = bible_db_path
        # Lithuanian Bible text: a chapter-only reference gets the verse the
        # speaker reads aloud. Optional; without it the chapter is reported.
        self.verse_text_db_path = verse_text_db_path
        self.server_url = server_url

        # Initialize components with bounded queues
        self.audio_capture = AudioCapture(queue_maxsize=self.config.audio_queue_maxsize)
        self.vad = create_vad(
            self.config.vad,
            aggressiveness=self.config.vad_aggressiveness,
            silero_threshold=self.config.silero_threshold,
            silero_neg_threshold=self.config.silero_neg_threshold,
        )
        # Whisper for every language, or GGUF models for Lithuanian (Whisper
        # then loads only once a non-Lithuanian utterance needs it).
        self._stt_models = engine_models(self.config.stt_engine)
        self.stt: SpeechToText | MultiModelSpeechToText
        if self._stt_models:
            self.stt = create_gguf_speech_to_text(
                self.config.stt_engine,
                {"kmynas": self.config.kmynas_model, "canary": self.config.canary_model},
                device=self.config.whisper_device,
                cpu_threads=self.config.cpu_threads,
                language=self.config.language,
                whisper_factory=self._create_whisper,
            )
        else:
            self.stt = self._create_whisper()

        # VAD queue for speech segments (bounded). Each item is
        # ``(language, frames)``: the language the utterance was captured in, so
        # a switch does not retroactively re-tag segments already queued.
        self.vad_queue: Queue[tuple[str, list[bytes]]] = Queue(
            maxsize=self.config.vad_queue_maxsize
        )

        # State tracking
        self.is_running = False
        self.frame_duration_ms = FRAME_DURATION_MS
        self._frame_duration_s = self.frame_duration_ms / 1000
        # Utterance assembly, shared with the benchmark (owned by the VAD thread).
        self.segmenter = Segmenter.from_config(self.vad.is_speech, self.config)

        # Metrics
        self.metrics = PipelineMetrics()
        self.last_metrics_log = time.time()

        # Reference delivery (dedup + retry-with-backoff)
        self.sender = ReferenceSender(server_url, self.config, self.metrics)
        # Confident transcripts for follow-along hints (best effort, never blocks).
        self.transcript_sender = TranscriptSender(server_url, self.config, self.metrics)

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

        # Capture restart after an unrequested ffmpeg exit (VAD thread). The lock
        # keeps stop() and a restart from interleaving, which could otherwise
        # start a new ffmpeg after stop() has already shut capture down.
        self._capture_lock = threading.Lock()
        self._capture_restart_attempt = 0
        self._capture_restarted_at_s: float | None = None

        # Health the operator should see (reported by the heartbeat thread).
        # Each field has one writer: capture state the VAD thread, the rest the
        # transcription thread; the heartbeat thread only reads them.
        self._capture_down = False
        # time.monotonic() of the last audio frame; a stalled ffmpeg delivers
        # nothing without exiting, so silence from capture is reported too.
        self._last_frame_at: float | None = None
        # time.monotonic() of the last frame that was sound, and whether the
        # silent-microphone warning is up (VAD thread, see _note_microphone_level).
        self._last_loud_at: float | None = None
        self._mic_silent_logged = False
        self._consecutive_transcription_errors = 0
        self._parser_problems: list[str] = []
        self.heartbeat: HeartbeatSender | None = (
            HeartbeatSender(server_url, self.config, self.health_report)
            if self.config.heartbeat_interval > 0
            else None
        )

        # Live session log, opened by start() when configured.
        self.session_log: SessionLog | None = None

        # Worker threads
        self.vad_thread: threading.Thread | None = None
        self.transcription_thread: threading.Thread | None = None

    def _create_whisper(self) -> SpeechToText:
        return SpeechToText(
            model_size=self.config.whisper_model,
            device=self.config.whisper_device,
            language=self.config.language,
            compute_type=self.config.whisper_compute_type,
            beam_size=self.config.beam_size,
            cpu_threads=self.config.cpu_threads,
        )

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
            self._last_frame_at = time.monotonic()
            self._last_loud_at = time.monotonic()
            self._mic_silent_logged = False

            if self.config.session_log_dir:
                self.session_log = self._open_session_log(self.config.session_log_dir)

            # Start audio capture
            self.audio_capture.start_capture(self.config.input_device)

            # Start reference delivery before anything can detect a reference
            self.sender.start()
            self.transcript_sender.start()

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

            # Report health to the server so the operator can see a failure
            if self.heartbeat is not None:
                self.heartbeat.start()
        except BaseException:
            # A partially started pipeline must not leave the control port bound,
            # capture running, or is_running stuck True for direct callers.
            self._rollback_start()
            raise

        logger.info("Audio pipeline started. Listening for Bible references...")

    def _rollback_start(self) -> None:
        """Unwind a failed :meth:`start`, preserving the original exception."""
        self.is_running = False
        if self.heartbeat is not None:
            self.heartbeat.stop()
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
        self.sender.stop()
        self.transcript_sender.stop()
        self._close_session_log()

    def _open_session_log(self, directory: str) -> SessionLog | None:
        """Open the session log; a failure only costs the log, never detection."""
        config = self.config
        header = {
            "stt_engine": config.stt_engine,
            "models": {
                name: os.path.basename(getattr(config, f"{name}_model") or "")
                for name in getattr(self, "_stt_models", [])
            },
            "whisper_model": config.whisper_model,
            "device": config.whisper_device,
            "language": config.language,
            "vad": config.vad,
        }
        try:
            return SessionLog(directory, config.session_audio, header, self._frame_duration_s)
        except Exception as e:
            logger.error(f"Could not open the session log in {directory}: {e}")
            return None

    def _close_session_log(self) -> None:
        session_log = getattr(self, "session_log", None)
        if session_log is not None:
            session_log.close()
            self.session_log = None

    def stop(self):
        """Stop the audio processing pipeline"""
        logger.info("Stopping audio pipeline...")
        self.is_running = False

        # Stop reporting first: the server then shows the pipeline as offline,
        # which is what it is about to be.
        if self.heartbeat is not None:
            self.heartbeat.stop()

        # Stop audio capture (after any restart in progress has finished, so it
        # cannot start ffmpeg again behind our back)
        with self._capture_lock:
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

        # Then delivery, once nothing can submit any more
        self.sender.stop()
        self.transcript_sender.stop()

        # The transcription thread closes its own thread-local BibleLookup on
        # exit (see _transcription_loop): stop() runs on the main thread whose
        # threading.local() namespace never holds it, so closing here would be
        # dead code.

        # Last: the transcription thread logs held references it flushes
        self._close_session_log()

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

    def health_report(self) -> dict:
        """What the heartbeat tells the server: the language and any problems.

        Each problem is one sentence for the operator, naming what has stopped
        working. An empty list means the pipeline is detecting normally (a quiet
        room is not a problem).
        """
        problems: list[str] = []
        # An uncaught exception ends a worker thread silently; this thread
        # would otherwise go on reporting a pipeline that detects nothing.
        for thread, role in (
            (self.vad_thread, "listening"),
            (self.transcription_thread, "transcription"),
        ):
            if self.is_running and thread is not None and not thread.is_alive():
                problems.append(f"The pipeline's {role} thread has stopped; restart the pipeline")
        if self._capture_down:
            problems.append("Audio capture stopped; the pipeline is trying to restart it")
        elif self.is_running and self._last_frame_at is not None:
            # Capture streams continuously (silence included), so a gap means
            # ffmpeg is stuck, not that the room is quiet.
            now = time.monotonic()
            silent_s = now - self._last_frame_at
            if silent_s > AUDIO_STALL_S:
                problems.append(f"No audio has arrived from the microphone for {silent_s:.0f} s")
            elif self._last_loud_at is not None and now - self._last_loud_at > SILENT_MIC_S:
                # Frames arrive but none has any sound in it: a dead or muted input.
                problems.append(
                    f"The microphone is silent: no sound for {now - self._last_loud_at:.0f} s. "
                    "Check that the right microphone is selected and not muted."
                )
        errors = self._consecutive_transcription_errors
        if errors:
            problems.append(
                f"Speech recognition failed on the last {errors} "
                f"utterance{'s' if errors != 1 else ''}"
            )
        delivery_problem = self.sender.delivery_problem
        if delivery_problem:
            problems.append(f"The last detected reference was not delivered: {delivery_problem}")
        problems.extend(self._parser_problems)
        return {
            "language": self.current_language,
            "interval_s": self.config.heartbeat_interval,
            "problems": problems,
        }

    def _consume_pending_language_switch(self) -> None:
        """Apply a pending language switch (VAD thread only).

        The VAD thread owns the segmenter, so only it may discard a partial
        utterance; the control thread merely records the request. Queued
        utterances are left alone.
        """
        with self._pending_language_lock:
            pending = self._pending_language
            self._pending_language = None

        if pending is None or pending == self._capture_language:
            return

        dropped = self.segmenter.reset()
        session_log = getattr(self, "session_log", None)
        if session_log is not None:
            session_log.event("language", language=pending, dropped_frames=dropped)
        if dropped:
            logger.info(
                "Language switched to '%s': discarding partial utterance (%d frames)",
                pending,
                dropped,
            )
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
                if self.audio_capture.failed and self.is_running:
                    self._restart_capture()
                else:
                    time.sleep(0.01)
                continue

            self._note_capture_healthy()
            now = time.monotonic()
            self._last_frame_at = now
            self._note_microphone_level(frame, now)
            self.metrics.audio_chunks_captured += 1
            session_log = getattr(self, "session_log", None)
            if session_log is not None:
                session_log.write_frame(frame)

            event = self.segmenter.push(frame)
            if event is not None:
                if event.kind == "start":
                    session_log = getattr(self, "session_log", None)
                    if session_log is not None:
                        session_log.speech_started(event.onset_frames)
                    logger.info("Voice activity detected, capturing utterance...")
                else:
                    self._queue_speech_segment(event)

            # Update queue depth metrics
            self.metrics.audio_queue_depth = self.audio_capture.audio_queue.qsize()
            self.metrics.vad_queue_depth = self.vad_queue.qsize()

            # Periodic metrics logging
            if time.time() - self.last_metrics_log > self.config.metrics_log_interval:
                self.metrics.log_summary()
                self.last_metrics_log = time.time()

    def _restart_capture(self) -> None:
        """Restart capture after ffmpeg exited on its own (VAD thread only).

        Without this, an unplugged microphone or a driver reset ended detection
        for the rest of the service while the process kept running, so neither
        the operator nor a supervisor could tell. The utterance in progress is
        closed and queued (it is real speech up to the failure), then ffmpeg is
        restarted with doubling delays until it starts or the pipeline stops.
        The delays carry over between failures until a restarted stream has
        delivered ``capture_healthy_s`` of audio, so a flapping device backs off.
        """
        self.metrics.capture_failures += 1
        self._capture_down = True
        event = self.segmenter.flush()
        if event is not None:
            self._queue_speech_segment(event)

        while self.is_running:
            delay = min(
                self.config.capture_restart_base_delay * 2**self._capture_restart_attempt,
                self.config.capture_restart_max_delay,
            )
            self._capture_restart_attempt += 1
            logger.error(
                f"Audio capture stopped unexpectedly; no references can be detected "
                f"until it restarts. Restarting in {delay:.0f}s "
                f"(attempt {self._capture_restart_attempt})"
            )
            if not self._wait_while_running(delay):
                return

            with self._capture_lock:
                if not self.is_running:
                    return
                self.audio_capture.stop_capture()
                try:
                    self.audio_capture.start_capture(self.config.input_device)
                except Exception as e:
                    logger.error(f"Audio capture restart failed: {e}")
                    continue

            self.metrics.capture_restarts += 1
            self._capture_down = False
            # The silence clock runs from the new stream, not from before the outage.
            self._last_loud_at = time.monotonic()
            self._mic_silent_logged = False
            session_log = getattr(self, "session_log", None)
            if session_log is not None:
                session_log.event("capture_restarted")
            self._capture_restarted_at_s = self.audio_capture.captured_seconds
            logger.warning("Audio capture restarted; listening for Bible references again")
            return

    def _note_capture_healthy(self) -> None:
        """Reset the restart backoff once a restarted stream has proven stable."""
        if self._capture_restarted_at_s is None:
            return
        streamed = self.audio_capture.captured_seconds - self._capture_restarted_at_s
        if streamed >= self.config.capture_healthy_s:
            self._capture_restart_attempt = 0
            self._capture_restarted_at_s = None

    def _note_microphone_level(self, frame: bytes, now: float) -> None:
        """Track whether the microphone is picking up sound (VAD thread only).

        Only the transitions are logged, never every frame: the warning once
        when the silence passes SILENT_MIC_S, and the all-clear when sound returns.
        """
        if frame_is_loud(frame):
            self._last_loud_at = now
            if self._mic_silent_logged:
                self._mic_silent_logged = False
                logger.info("The microphone is picking up sound again")
        elif (
            not self._mic_silent_logged
            and self._last_loud_at is not None
            and now - self._last_loud_at > SILENT_MIC_S
        ):
            self._mic_silent_logged = True
            logger.warning(
                f"The microphone is silent: no sound above {SOUND_LEVEL_DBFS:.0f} dBFS for "
                f"{SILENT_MIC_S:.0f} s. Check that the right microphone is selected "
                f"(--input-device) and not muted."
            )

    def _wait_while_running(self, seconds: float) -> bool:
        """Sleep up to ``seconds``, waking early on stop; return whether still running."""
        deadline = time.monotonic() + seconds
        while self.is_running and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return self.is_running

    def _queue_speech_segment(self, event: SegmentEvent) -> None:
        """Queue a closed utterance tagged with the capture language."""
        frames = event.frames
        utterance_seconds = self._frame_duration_s * len(frames)
        if event.kind == "end":
            # Before the put: once queued, the transcription thread may log
            # the utterance at once, and must find its position.
            session_log = getattr(self, "session_log", None)
            if session_log is not None:
                session_log.utterance_queued(frames, event.trailing_silent_frames)
            try:
                self.vad_queue.put((self._capture_language, frames), timeout=0.1)
                self.metrics.vad_segments_detected += 1
                logger.info(
                    f"Utterance ended ({event.reason}): {utterance_seconds:.2f}s of audio "
                    f"({len(frames)} frames, {self._capture_language}) "
                    f"queued for transcription"
                )
            except Full:
                if session_log is not None:
                    session_log.utterance_dropped(frames)
                logger.warning("VAD queue full, dropping speech segment (slow transcription)")
        else:
            logger.debug(
                f"Ignoring {utterance_seconds:.2f}s blip "
                f"(fewer than {self.segmenter.min_speech_frames} voiced frames)"
            )

    def _transcription_loop(self):
        """Transcription processing loop - transcribes speech segments"""
        # Initialize thread-local Bible lookup and one parser per language
        self._init_thread_local_parsers()

        try:
            while self.is_running:
                try:
                    # Get speech segment (with the language it was captured in)
                    language, speech_frames = self.vad_queue.get(timeout=0.5)
                except Empty:
                    continue

                # Process the utterance
                self._process_utterance(language, speech_frames)
        finally:
            # Flush held chapter-only references so stop() does not drop them,
            # then close the thread-local lookup here, the only thread that can
            # see it. Bounded gap, accepted: if a Whisper call is still in
            # flight past stop()'s 2 s join, the sender is already stopped when
            # this flush lands and the reference is lost. Rare and bounded by
            # the join timeout; shutdown must not hang on a stuck transcription.
            try:
                parsers = getattr(self._local, "streaming_parsers", None) or {}
                for held_language in list(parsers):
                    flushed = self._flush_held(held_language)
                    session_log = getattr(self, "session_log", None)
                    if session_log is not None:
                        session_log.references("shutdown_flush", flushed)
                    for ref in flushed:
                        logger.info("Flushing held reference on shutdown: %s", ref)
                        try:
                            self.sender.submit(ref)
                        except Exception as e:
                            logger.warning(f"Error submitting flushed reference: {e}")
            finally:
                lookup = getattr(self._local, "bible_lookup", None)
                if lookup is not None:
                    try:
                        lookup.close()
                    except Exception as e:
                        logger.warning(f"Error closing Bible lookup: {e}")
                    self._local.bible_lookup = None

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
        problems: list[str] = []
        if validator is None:
            # Parsing still works, but nothing checks that a detected verse
            # exists before it reaches the operator.
            problems.append(
                "The pipeline could not open the Bible database, so detections "
                "are not checked against it"
            )

        verse_text = self._load_verse_text()

        # English and Lithuanian parsing stay separate implementations; the
        # language selects which tables a parser is built with. GGUF models
        # transcribe Lithuanian only, so other languages' parsers read Whisper.
        models = getattr(self, "_stt_models", [])
        parsers: dict[str, StreamingParser] = {}
        # Two-model mode: the second model's transcripts get their own parsers,
        # so its context never mixes with the first model's.
        alternate_parsers: dict[str, StreamingParser] = {}
        for language in SUPPORTED_LANGUAGES:
            gguf = bool(models) and language in GGUF_LANGUAGES
            try:
                source = models[0] if gguf else "whisper"
                parsers[language] = self._make_parser(language, validator, verse_text, source)
                if gguf and len(models) > 1:
                    alternate_parsers[language] = self._make_parser(
                        language, validator, verse_text, models[1]
                    )
            except Exception as e:
                logger.error(f"Failed to initialize '{language}' parser: {e}")
                problems.append(
                    f"The '{language}' reference parser failed to start, so no "
                    f"references are detected in that language"
                )

        self._parser_problems = problems
        self._local.streaming_parsers = parsers
        self._local.alternate_parsers = alternate_parsers
        self._local.last_parser_language = None
        logger.debug(
            f"Thread {threading.current_thread().name}: Initialized parsers for "
            f"{', '.join(parsers) or 'no languages'}"
        )

    @staticmethod
    def _make_parser(
        language: str,
        validator,
        verse_text: VerseTextIndex | None,
        source: str,
    ) -> StreamingParser:
        """One streaming parser for ``source``'s transcripts in ``language``."""
        parser = ReferenceParser(validator=validator, language=language)
        lt_text = verse_text if language == "lt" else None
        rewrite = spelling_map(source, language)
        # Hold a chapter-only ref one utterance in case the verse follows.
        if rewrite is not None:
            return MappedStreamingParser(
                parser,
                language=language,
                hold_chapter_only=True,
                verse_text=lt_text,
                spelling_map=rewrite,
            )
        return StreamingParser(
            parser, language=language, hold_chapter_only=True, verse_text=lt_text
        )

    def _flush_held(self, language: str) -> list[Reference]:
        """Release ``language``'s held references (both models' in two-model mode)."""
        parsers = getattr(self._local, "streaming_parsers", None) or {}
        alternates = getattr(self._local, "alternate_parsers", None) or {}
        parser = parsers.get(language)
        held = parser.flush() if parser is not None else []
        alternate = alternates.get(language)
        if alternate is None:
            return held
        return merge_agreement(held, alternate.flush())

    def _alternate_parser(self, language: str) -> StreamingParser | None:
        """The second model's parser for ``language`` (two-model mode only)."""
        return (getattr(self._local, "alternate_parsers", None) or {}).get(language)

    def _load_verse_text(self) -> VerseTextIndex | None:
        """The Lithuanian verse-text index, or None when it is not available."""
        path = self.verse_text_db_path
        if not path:
            return None
        if not Path(path).is_file():
            logger.info(
                "Verse text database %s not found: chapter-only references are "
                "reported without picking the verse read aloud",
                path,
            )
            return None
        try:
            return VerseTextIndex.from_sqlite(path)
        except Exception as e:
            logger.warning(f"Failed to load verse text from {path}: {e}")
            return None

    def _select_parser(self, language: str) -> StreamingParser | None:
        """Select the parser for ``language``, resetting context on a switch."""
        parsers: dict[str, StreamingParser] = getattr(self._local, "streaming_parsers", None) or {}
        parser = parsers.get(language)
        if parser is None:
            logger.warning("No streaming parser available for language '%s'", language)
            return None

        last = getattr(self._local, "last_parser_language", None)
        if language != last:
            # A held chapter-only reference waits one utterance for its verse.
            # A language switch means that verse will never come in the old
            # language, so flush the previous parser instead of leaving its
            # held reference to be dropped by a later reset.
            if last is not None:
                for ref in self._flush_held(last):
                    logger.info(
                        "Flushing held reference on language switch: %s",
                        ref,
                    )
                    self.sender.submit(ref)
            # Previous-language context (carry tokens + inferred chapter/verse)
            # must not leak across the switch.
            for ref in self._flush_held(language):
                logger.info(
                    "Flushing held reference on language switch: %s",
                    ref,
                )
                self.sender.submit(ref)
            parser.reset()
            alternate = self._alternate_parser(language)
            if alternate is not None:
                alternate.reset()
            self._local.last_parser_language = language
            logger.info("Selected '%s' parser (context reset)", language)

        return parser

    def _process_utterance(self, language: str, speech_frames: list[bytes]):
        """Process a completed utterance captured in ``language``"""
        result, references = self._detect_references(language, speech_frames)
        session_log = getattr(self, "session_log", None)
        if session_log is not None:
            session_log.utterance(speech_frames, language, result, references)

    def _detect_references(
        self, language: str, speech_frames: list[bytes]
    ) -> tuple[Transcription | None, list[Reference]]:
        """Transcribe, parse and send one utterance; returns what was heard and sent."""
        audio_seconds = self._frame_duration_s * len(speech_frames)
        logger.info(
            f"Processing utterance ({len(speech_frames)} frames, {audio_seconds:.2f}s audio, "
            f"language={language})..."
        )

        # Transcribe with the language this utterance was captured in, so a
        # runtime switch cannot re-language a segment that was already queued.
        self.metrics.transcription_attempts += 1
        result = self.stt.transcribe_utterance(b"".join(speech_frames), language)
        transcript, duration_ms = result.text, result.duration_ms
        # Two-model mode: the second model's transcript of the same audio.
        alternate = result.alternate
        if result.failed:
            self._consecutive_transcription_errors += 1
        else:
            self._consecutive_transcription_errors = 0

        if transcript is None and (alternate is None or alternate.text is None):
            self.metrics.transcription_failures += 1
            logger.debug("No transcription produced")
            return result, []

        self.metrics.transcription_successes += 1
        self.metrics.record_transcription_duration(duration_ms)
        if alternate is None:
            logger.info(f"Transcription ({duration_ms:.0f}ms): '{transcript}'")
        else:
            logger.info(
                f"Transcription ({duration_ms:.0f}ms): {result.source}: '{transcript}' | "
                f"{alternate.source}: '{alternate.text}'"
            )

        # A confident transcript also goes to the server, which may hint the verse the
        # speaker reads next. A shaky one never does: its words may not be what was said.
        confident = [
            r.text
            for r in (result, alternate)
            if r is not None and r.text and r.low_confidence_reason is None
        ]
        if confident:
            self.transcript_sender.submit(confident[0], language)

        # Process with the parser for the utterance's capture language
        parser = self._select_parser(language)
        if parser is None:
            logger.warning("Streaming parser not initialized, cannot detect references")
            return result, []

        # Wallclock-stamped so a book/chapter context older than the
        # TTL is dropped instead of attaching to stray numbers.
        now = time.time()
        references = self._parse_transcription(parser, result, now)
        if alternate is not None:
            alternate_parser = self._alternate_parser(language)
            alternate_refs = (
                self._parse_transcription(alternate_parser, alternate, now)
                if alternate_parser is not None
                else None
            )
            if references is None and alternate_refs is None:
                return result, []
            # Only references both models found keep their confidence; a
            # parser error on one model still leaves the other's (as low).
            references = merge_agreement(references or [], alternate_refs or [])
        elif references is None:
            return result, []

        if not references:
            logger.debug("No Bible references detected in transcription")
            return result, []

        self.metrics.references_detected += len(references)
        logger.info(f"Found {len(references)} Bible reference(s):")
        for ref in references:
            logger.info(
                f"  - {ref} [{ref.confidence}]" + (" (from context)" if ref.inferred else "")
            )

            # Queue for the sender thread (deduplicated); never blocks on the server
            self.sender.submit(ref)
        return result, references

    def _parse_transcription(
        self, parser: StreamingParser, result: Transcription, now: float
    ) -> list[Reference] | None:
        """One model's references in its transcript, each once; None on a parser error.

        References from a shaky decode are marked "low".
        """
        if result.text is None:
            return []
        try:
            references = parser.feed(result.text, now)
        except Exception as e:
            logger.error(f"Error parsing references: {e}", exc_info=True)
            return None

        # One utterance can legitimately mention the same book/chapter more than
        # once ("Romans 8:28 ... Romans 8:28"), but a transcription that yields the
        # same reference dozens of times is a decoder artifact. Collapse them here
        # so logs and metrics describe what was actually heard.
        unique: list[Reference] = []
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
        if result.low_confidence_reason is not None and unique:
            self.metrics.references_low_confidence += len(unique)
            logger.warning(
                f"Low-confidence transcription ({result.low_confidence_reason}): "
                f"marking {len(unique)} reference(s) as low confidence"
            )
            for ref in unique:
                ref.confidence = "low"
        return unique
