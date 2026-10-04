"""Main audio processing pipeline for BibleLive."""

import logging
import threading
import time
from queue import Empty, Full, Queue

from core.bible_lookup import BibleLookup
from core.verse_parser import ReferenceParser, StreamingParser

from .capture import AudioCapture
from .config import AudioPipelineConfig
from .metrics import PipelineMetrics
from .sender import ReferenceSender
from .stt import SpeechToText
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

        # VAD queue for speech segments (bounded)
        self.vad_queue: Queue[list[bytes]] = Queue(maxsize=self.config.vad_queue_maxsize)

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

        # Thread-local storage for BibleLookup instances
        self._local = threading.local()

        # Worker threads
        self.vad_thread: threading.Thread | None = None
        self.transcription_thread: threading.Thread | None = None

    def start(self):
        """Start the audio processing pipeline"""
        logger.info("Starting BibleLive Audio Pipeline...")
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

        logger.info("Audio pipeline started. Listening for Bible references...")

    def stop(self):
        """Stop the audio processing pipeline"""
        logger.info("Stopping audio pipeline...")
        self.is_running = False

        # Stop audio capture
        self.audio_capture.stop_capture()

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

    def _vad_loop(self):
        """VAD processing loop - detects speech segments"""
        frame_duration_ms = self.frame_duration_ms

        while self.is_running:
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
                        utterance_seconds = self._frame_duration_s * len(self.speech_frames)
                        if len(self.speech_frames) >= self.min_speech_frames:
                            try:
                                self.vad_queue.put(self.speech_frames.copy(), timeout=0.1)
                                self.metrics.vad_segments_detected += 1
                                logger.info(
                                    f"Utterance ended: {utterance_seconds:.2f}s of speech "
                                    f"({len(self.speech_frames)} frames) queued for transcription"
                                )
                            except Full:
                                logger.warning(
                                    "VAD queue full, dropping speech segment (slow transcription)"
                                )
                        else:
                            logger.debug(
                                f"Ignoring {utterance_seconds:.2f}s blip "
                                f"({len(self.speech_frames)} frames < "
                                f"{self.min_speech_frames} minimum)"
                            )

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

    def _transcription_loop(self):
        """Transcription processing loop - transcribes speech segments"""
        # Initialize thread-local Bible lookup and parser
        self._init_thread_local_parser()

        while self.is_running:
            try:
                # Get speech segment from queue
                speech_frames = self.vad_queue.get(timeout=0.5)
            except Empty:
                continue

            # Process the utterance
            self._process_utterance(speech_frames)

    def _init_thread_local_parser(self):
        """Initialize thread-local Bible lookup and streaming parser"""
        if not hasattr(self._local, "bible_lookup"):
            try:
                self._local.bible_lookup = BibleLookup(self.bible_db_path, readonly=True)
                logger.debug(f"Thread {threading.current_thread().name}: Initialized Bible lookup")
            except Exception as e:
                logger.error(
                    f"Thread {threading.current_thread().name}: Failed to initialize Bible lookup: {e}"
                )
                self._local.bible_lookup = None
                self._local.streaming_parser = None
                return

            # Create parser with validator for this thread; language follows
            # the configured spoken language so "lt" transcripts parse with
            # the Lithuanian book names/grammar.
            try:
                validator = (
                    self._local.bible_lookup.get_validator_hook()
                    if self._local.bible_lookup.conn
                    else None
                )
                parser = ReferenceParser(validator=validator, language=self.config.language)
                self._local.streaming_parser = StreamingParser(parser)
                logger.debug(
                    f"Thread {threading.current_thread().name}: Initialized streaming parser"
                )
            except Exception as e:
                logger.error(f"Failed to initialize parser: {e}")
                self._local.streaming_parser = None

    def _process_utterance(self, speech_frames: list[bytes]):
        """Process a completed utterance (speech segment)"""
        audio_seconds = self._frame_duration_s * len(speech_frames)
        logger.info(
            f"Processing utterance ({len(speech_frames)} frames, {audio_seconds:.2f}s audio)..."
        )

        # Add all frames to STT buffer
        for frame in speech_frames:
            self.stt.add_audio_frame(frame)

        # Transcribe with timing
        self.metrics.transcription_attempts += 1
        transcript, duration_ms = self.stt.transcribe_buffer()

        if transcript is None:
            self.metrics.transcription_failures += 1
            logger.debug("No transcription produced")
            return

        self.metrics.transcription_successes += 1
        self.metrics.record_transcription_duration(duration_ms)
        logger.info(f"Transcription ({duration_ms:.0f}ms): '{transcript}'")

        # Process with StreamingParser (thread-local)
        if not self._local.streaming_parser:
            logger.warning("Streaming parser not initialized, cannot detect references")
            return

        try:
            references = self._local.streaming_parser.feed(transcript)
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

        self.metrics.references_detected += len(unique)
        logger.info(f"Found {len(unique)} Bible reference(s):")
        for ref in unique:
            logger.info(
                f"  - {ref} [{ref.confidence}]" + (" (from context)" if ref.inferred else "")
            )

            # Send to BibleLive server with deduplication
            self.sender.send(ref)
