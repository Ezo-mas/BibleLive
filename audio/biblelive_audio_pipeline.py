#!/usr/bin/env python3
"""
BibleLive Audio Processing Pipeline
Integrates ffmpeg audio capture, WebRTC VAD, faster-whisper STT,
and StreamingParser to process live audio for Bible reference detection.
"""

import hashlib
import logging
import random
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Optional

import numpy as np
import requests
import webrtcvad
from faster_whisper import WhisperModel

# Add the parent directory to sys.path to allow imports from core
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.bible_lookup import BibleLookup
from core.verse_parser import StreamingParser

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ============================================================================
# Metrics and Observability
# ============================================================================


@dataclass
class PipelineMetrics:
    """Track audio pipeline metrics for observability."""

    # Counters
    audio_chunks_captured: int = 0
    vad_segments_detected: int = 0
    transcription_attempts: int = 0
    transcription_successes: int = 0
    transcription_failures: int = 0
    references_detected: int = 0
    references_sent: int = 0
    references_failed: int = 0
    references_deduplicated: int = 0
    api_retries: int = 0

    # Latency tracking (moving averages)
    transcription_duration_ms: list[float] = field(default_factory=list)
    api_request_duration_ms: list[float] = field(default_factory=list)

    # Queue depth tracking
    audio_queue_depth: int = 0
    vad_queue_depth: int = 0

    def record_transcription_duration(self, duration_ms: float) -> None:
        """Record transcription duration (keep last 100)."""
        self.transcription_duration_ms.append(duration_ms)
        if len(self.transcription_duration_ms) > 100:
            self.transcription_duration_ms.pop(0)

    def record_api_duration(self, duration_ms: float) -> None:
        """Record API request duration (keep last 100)."""
        self.api_request_duration_ms.append(duration_ms)
        if len(self.api_request_duration_ms) > 100:
            self.api_request_duration_ms.pop(0)

    def get_avg_transcription_duration(self) -> float:
        """Get average transcription duration in ms."""
        return (
            sum(self.transcription_duration_ms) / len(self.transcription_duration_ms)
            if self.transcription_duration_ms
            else 0.0
        )

    def get_avg_api_duration(self) -> float:
        """Get average API request duration in ms."""
        return (
            sum(self.api_request_duration_ms) / len(self.api_request_duration_ms)
            if self.api_request_duration_ms
            else 0.0
        )

    def log_summary(self) -> None:
        """Log metrics summary."""
        logger.info("=== Pipeline Metrics ===")
        logger.info(f"  Audio chunks: {self.audio_chunks_captured}")
        logger.info(f"  VAD segments: {self.vad_segments_detected}")
        logger.info(
            f"  Transcriptions: {self.transcription_successes}/{self.transcription_attempts} "
            f"({self.transcription_failures} failed)"
        )
        logger.info(f"  Avg transcription time: {self.get_avg_transcription_duration():.1f}ms")
        logger.info(f"  References detected: {self.references_detected}")
        logger.info(
            f"  References sent: {self.references_sent} "
            f"({self.references_failed} failed, {self.references_deduplicated} deduplicated)"
        )
        logger.info(f"  API retries: {self.api_retries}")
        logger.info(f"  Avg API request time: {self.get_avg_api_duration():.1f}ms")
        logger.info(
            f"  Queue depths - Audio: {self.audio_queue_depth}, VAD: {self.vad_queue_depth}"
        )


# ============================================================================
# Configuration
# ============================================================================


@dataclass
class AudioPipelineConfig:
    """Configuration for audio pipeline."""

    # Queue sizes (bounded to prevent memory issues)
    audio_queue_maxsize: int = 1000  # ~10 seconds at 10ms chunks
    vad_queue_maxsize: int = 100  # ~100 speech segments

    # Retry configuration
    api_max_retries: int = 3
    api_retry_base_delay: float = 0.5  # seconds
    api_retry_max_delay: float = 5.0  # seconds
    api_timeout: float = 5.0  # seconds

    # Deduplication window
    dedup_window_seconds: float = 5.0  # Don't send same reference within 5s

    # Metrics logging interval
    metrics_log_interval: int = 60  # Log metrics every 60 seconds


class AudioCapture:
    """Handles audio capture using ffmpeg with proper stderr handling and bounded queues."""

    def __init__(self, sample_rate: int = 16000, queue_maxsize: int = 1000):
        self.sample_rate = sample_rate
        self.audio_queue = Queue(maxsize=queue_maxsize)
        self.is_running = False
        self.ffmpeg_process = None
        self.capture_thread = None
        self.stderr_thread = None

    def start_capture(self, device_index: Optional[int] = None):
        """Start capturing audio from microphone"""
        self.is_running = True

        # Build ffmpeg command
        if sys.platform == "darwin":  # macOS
            input_args = ["-f", "avfoundation", "-i", f":{device_index or ''}"]
        elif sys.platform.startswith("linux"):  # Linux
            input_args = ["-f", "pulse", "-i", "default"]
        elif sys.platform == "win32":  # Windows
            input_args = ["-f", "dshow", "-i", f"audio={device_index or 'default'}"]
        else:
            raise ValueError(f"Unsupported platform: {sys.platform}")

        ffmpeg_cmd = [
            "ffmpeg",
            *input_args,
            "-ac",
            "1",  # Mono
            "-ar",
            str(self.sample_rate),  # Sample rate
            "-f",
            "s16le",  # 16-bit little-endian PCM
            "-",  # Output to stdout
        ]

        logger.info(f"Starting ffmpeg capture: {' '.join(ffmpeg_cmd)}")

        try:
            self.ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
            )
        except FileNotFoundError:
            raise RuntimeError("ffmpeg not found. Please install ffmpeg and ensure it's in PATH")
        except subprocess.SubprocessError as e:
            raise RuntimeError(f"Failed to start ffmpeg: {e}")

        # Start thread to consume stderr (prevents blocking)
        self.stderr_thread = threading.Thread(
            target=self._consume_stderr, daemon=True, name="ffmpeg-stderr"
        )
        self.stderr_thread.start()

        # Start thread to read audio data
        self.capture_thread = threading.Thread(
            target=self._read_audio_data, daemon=True, name="audio-capture"
        )
        self.capture_thread.start()

    def _consume_stderr(self):
        """Consume ffmpeg stderr to prevent pipe from filling and blocking."""
        if not self.ffmpeg_process or not self.ffmpeg_process.stderr:
            return

        try:
            for line in iter(self.ffmpeg_process.stderr.readline, b""):
                if not self.is_running:
                    break
                # Log ffmpeg output at debug level (can be noisy)
                line_str = line.decode("utf-8", errors="replace").strip()
                if line_str:
                    logger.debug(f"ffmpeg: {line_str}")
        except OSError as e:
            # Pipe closed, expected during shutdown
            logger.debug(f"ffmpeg stderr pipe closed: {e}")
        except Exception as e:
            logger.warning(f"Error consuming ffmpeg stderr: {e}")

    def _read_audio_data(self):
        """Read audio data from ffmpeg stdout and put in queue"""
        chunk_size = 2 * self.sample_rate // 100  # 10ms chunks (2 bytes per sample)

        try:
            while self.is_running and self.ffmpeg_process:
                try:
                    raw_data = self.ffmpeg_process.stdout.read(chunk_size)
                    if not raw_data:
                        logger.info("ffmpeg stdout closed, stopping audio capture")
                        break

                    # Use non-blocking put with timeout to handle backpressure
                    try:
                        self.audio_queue.put(raw_data, timeout=0.1)
                    except Full:
                        logger.warning("Audio queue full, dropping audio chunk (slow consumer)")

                except OSError as e:
                    logger.error(f"OS error reading ffmpeg stdout: {e}")
                    break

        except Exception as e:
            logger.error(f"Unexpected error reading audio data: {e}", exc_info=True)
        finally:
            logger.info("Audio capture thread stopped")

    def stop_capture(self):
        """Stop audio capture and cleanup resources"""
        logger.info("Stopping audio capture...")
        self.is_running = False

        if self.ffmpeg_process:
            try:
                self.ffmpeg_process.terminate()
                # Give it 2 seconds to terminate gracefully
                self.ffmpeg_process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                logger.warning("ffmpeg didn't terminate gracefully, killing...")
                self.ffmpeg_process.kill()
                self.ffmpeg_process.wait()
            except Exception as e:
                logger.error(f"Error terminating ffmpeg: {e}")
            finally:
                self.ffmpeg_process = None

        # Wait for threads to finish
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        if self.stderr_thread and self.stderr_thread.is_alive():
            self.stderr_thread.join(timeout=2.0)

        logger.info("Audio capture stopped")

    def get_audio_frame(self, frame_duration_ms: int = 30) -> Optional[bytes]:
        """Get audio frame of specified duration"""
        # Calculate bytes needed for frame duration
        bytes_per_sample = 2  # 16-bit
        samples_per_frame = int(self.sample_rate * frame_duration_ms / 1000)
        bytes_per_frame = samples_per_frame * bytes_per_sample

        # Collect enough data for a frame
        frame_data = b""
        while len(frame_data) < bytes_per_frame and self.is_running:
            try:
                data = self.audio_queue.get(timeout=0.1)
                frame_data += data
            except Empty:
                if not self.is_running:
                    break
                continue

        if len(frame_data) >= bytes_per_frame:
            return frame_data[:bytes_per_frame]
        return None


class VoiceActivityDetector:
    """Handles voice activity detection using WebRTC VAD"""

    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2):
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        self.frame_duration_ms = 30  # WebRTC VAD requires 10, 20, or 30 ms frames

    def is_speech(self, frame: bytes) -> bool:
        """Check if audio frame contains speech"""
        try:
            return self.vad.is_speech(frame, self.sample_rate)
        except webrtcvad.Error as e:
            logger.debug(f"VAD error (invalid frame): {e}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected VAD error: {e}")
            return False


class SpeechToText:
    """Handles speech-to-text transcription using faster-whisper"""

    def __init__(self, model_size: str = "tiny", device: str = "cpu"):
        logger.info(f"Loading Whisper model '{model_size}' on {device}...")
        try:
            self.model = WhisperModel(model_size, device=device, compute_type="int8")
            logger.info("Whisper model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise

        # Audio buffer for accumulating speech
        self.audio_buffer = []
        self.sample_rate = 16000

    def add_audio_frame(self, frame: bytes):
        """Add audio frame to buffer for transcription"""
        # Convert bytes to numpy array
        audio_np = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        self.audio_buffer.extend(audio_np)

    def transcribe_buffer(self) -> tuple[Optional[str], float]:
        """
        Transcribe accumulated audio buffer and clear it.

        Returns:
            Tuple of (transcribed_text, duration_ms)
        """
        if not self.audio_buffer:
            return None, 0.0

        # Convert buffer to numpy array
        audio_np = np.array(self.audio_buffer, dtype=np.float32)

        # Clear buffer for next utterance
        self.audio_buffer = []

        # Skip if audio is too short (less than 0.5 seconds)
        if len(audio_np) < self.sample_rate * 0.5:
            return None, 0.0

        start_time = time.time()

        try:
            # Transcribe with Whisper
            segments, info = self.model.transcribe(
                audio_np,
                language="en",
                beam_size=5,
                best_of=5,
                temperature=0.0,
                compression_ratio_threshold=2.4,
                log_prob_threshold=-1.0,
                no_speech_threshold=0.6,
                condition_on_previous_text=False,
            )

            # Combine all segments
            text = " ".join([segment.text.strip() for segment in segments]).strip()

            # Apply post-processing fixes for common transcription errors
            text = self._fix_common_transcription_errors(text)

            duration_ms = (time.time() - start_time) * 1000

            return text if text else None, duration_ms

        except RuntimeError as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Whisper runtime error: {e}")
            return None, duration_ms
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Unexpected transcription error: {e}", exc_info=True)
            return None, duration_ms

    def _fix_common_transcription_errors(self, text: str) -> str:
        """Fix common transcription errors in Bible references"""
        import re

        # Fix common number combinations that get merged
        # Examples: "1 12" -> "1 12" (should stay as is, but sometimes becomes "112")
        # We'll look for patterns where numbers are incorrectly combined

        # Pattern: word followed by merged numbers (e.g., "mark112" -> "mark 1 12")
        # But we need to be careful not to break actual words

        # More conservative approach: fix specific known issues
        corrections = {
            r"\b(john|jude|genesis|exodus|leviticus|numbers|deuteronomy|joshua|judges|ruth|1\s?samuel|2\s?samuel|1\s?kings|2\s?kings|1\s?chronicles|2\s?chronicles|ezra|nehemiah|esther|job|psalms|proverbs|ecclesiastes|song\s?of\s?solomon|isaiah|jeremiah|lamentations|ezekiel|daniel|hosea|joel|amos|obadiah|jonah|micah|nahum|habakkuk|zephaniah|haggai|zechariah|malachi|matthew|mark|luke|john|acts|romans|1\s?corinthians|2\s?corinthians|galatians|ephesians|philippians|colossians|1\s?thessalonians|2\s?thessalonians|1\s?timon|2\s?timon|titus|philemon|hebrews|james|1\s?peter|2\s?peter|1\s?john|2\s?john|3\s?john|jude|revelation)\s*(\d+)\s*(\d+)\b": r"\1 \2:\3",
            r"\b(john|jude|genesis|exodus|leviticus|numbers|deuteronomy|joshua|judges|ruth|1\s?samuel|2\s?samuel|1\s?kings|2\s?kings|1\s?chronicles|2\s?chronicles|ezra|nehemiah|esther|job|psalms|proverbs|ecclesiastes|song\s?of\s?solomon|isaiah|jeremiah|lamentations|ezekiel|daniel|hosea|joel|amos|obadiah|jonah|micah|nahum|habakkuk|zephaniah|haggai|zechariah|malachi|matthew|mark|luke|john|acts|romans|1\s?corinthians|2\s?corinthians|galatians|ephesians|philippians|colossians|1\s?thessalonians|2\s?thessalonians|1\s?timon|2\s?timon|titus|philemon|hebrews|james|1\s?peter|2\s?peter|1\s?john|2\s?john|3\s?john|jude|revelation)\s*(\d+):(\d+)\s*(\d+)\b": r"\1 \2:\3-\4",
        }

        # Apply corrections
        for pattern, replacement in corrections.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # Handle specific common misrecognitions
        # "mark 1 12" sometimes becomes "mark 112" - try to split numbers that look like chapter:verse
        # Look for patterns like "word digit digit digit" where the last two digits could be a verse
        # This is tricky and might cause false positives, so we'll be conservative

        # Another approach: look for sequences of 3+ digits that might be chapter:verse combos
        # But only apply in clear Bible reference contexts

        return text


class BibleLiveAudioProcessor:
    """Main audio processing pipeline for BibleLive with reliability improvements"""

    def __init__(
        self,
        bible_db_path: str = "bible_data/biblelive.sqlite",
        kjv_path: str = "data/kjv.json",
        server_url: str = "http://localhost:8000",
        config: Optional[AudioPipelineConfig] = None,
    ):
        # Configuration
        self.config = config or AudioPipelineConfig()
        self.bible_db_path = bible_db_path
        self.kjv_path = kjv_path
        self.server_url = server_url

        # Initialize components with bounded queues
        self.audio_capture = AudioCapture(queue_maxsize=self.config.audio_queue_maxsize)
        self.vad = VoiceActivityDetector()
        self.stt = SpeechToText()

        # VAD queue for speech segments (bounded)
        self.vad_queue = Queue(maxsize=self.config.vad_queue_maxsize)

        # State tracking
        self.is_running = False
        self.speech_frames = []
        self.silent_frames = 0
        self.max_silent_frames = 50  # ~1.5 seconds of silence to end utterance
        self.min_speech_frames = 10  # Minimum frames to consider as speech (~300ms)

        # Deduplication: track recently sent references
        self.recent_references: dict[str, float] = {}  # ref_hash -> timestamp

        # Metrics
        self.metrics = PipelineMetrics()
        self.last_metrics_log = time.time()

        # Thread-local storage for BibleLookup instances
        self._local = threading.local()

        # Worker threads
        self.vad_thread = None
        self.transcription_thread = None

    def start(self):
        """Start the audio processing pipeline"""
        logger.info("Starting BibleLive Audio Pipeline...")
        self.is_running = True

        # Start audio capture
        self.audio_capture.start_capture()

        # Start VAD processing thread
        self.vad_thread = threading.Thread(target=self._vad_loop, daemon=True, name="vad-processor")
        self.vad_thread.start()

        # Start transcription thread
        self.transcription_thread = threading.Thread(
            target=self._transcription_loop, daemon=True, name="transcription-processor"
        )
        self.transcription_thread.start()

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
        frame_duration_ms = 30

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
                self.silent_frames = 0
                self.speech_frames.append(frame)
            else:
                # Silence detected
                if self.speech_frames:
                    # We were in speech, now silent - check if utterance ended
                    self.silent_frames += 1

                    if self.silent_frames > self.max_silent_frames:
                        # End of utterance detected - send to transcription queue
                        if len(self.speech_frames) >= self.min_speech_frames:
                            try:
                                self.vad_queue.put(self.speech_frames.copy(), timeout=0.1)
                                self.metrics.vad_segments_detected += 1
                            except Full:
                                logger.warning(
                                    "VAD queue full, dropping speech segment (slow transcription)"
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

            # Create parser with validator for this thread
            from core.verse_parser import ReferenceParser

            try:
                validator = (
                    self._local.bible_lookup.get_validator_hook()
                    if self._local.bible_lookup.conn
                    else None
                )
                parser = ReferenceParser(validator=validator)
                self._local.streaming_parser = StreamingParser(parser)
                logger.debug(
                    f"Thread {threading.current_thread().name}: Initialized streaming parser"
                )
            except Exception as e:
                logger.error(f"Failed to initialize parser: {e}")
                self._local.streaming_parser = None

    def _process_utterance(self, speech_frames: list[bytes]):
        """Process a completed utterance (speech segment)"""
        logger.info(f"Processing utterance ({len(speech_frames)} frames)...")

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

        if references:
            logger.info(f"Found {len(references)} Bible reference(s):")
            for ref in references:
                self.metrics.references_detected += 1
                logger.info(
                    f"  - {ref} [{ref.confidence}]" + (" (from context)" if ref.inferred else "")
                )

                # Send to BibleLive server with deduplication
                self._send_reference_to_server(ref)
        else:
            logger.debug("No Bible references detected in transcription")

    def _get_reference_hash(self, ref) -> str:
        """Generate a hash for deduplication"""
        key = f"{ref.book}:{ref.chapter}:{ref.verse_start}-{ref.verse_end}"
        return hashlib.md5(key.encode()).hexdigest()[:16]

    def _is_duplicate(self, ref) -> bool:
        """Check if reference was recently sent (within dedup window)"""
        ref_hash = self._get_reference_hash(ref)
        current_time = time.time()

        # Clean up old entries
        expired_keys = [
            k
            for k, t in self.recent_references.items()
            if current_time - t > self.config.dedup_window_seconds
        ]
        for k in expired_keys:
            del self.recent_references[k]

        # Check if this reference was recently sent
        if ref_hash in self.recent_references:
            time_since = current_time - self.recent_references[ref_hash]
            if time_since < self.config.dedup_window_seconds:
                return True

        # Record this reference
        self.recent_references[ref_hash] = current_time
        return False

    def _send_reference_to_server(self, ref):
        """Send a Bible reference to the BibleLive server with retry logic"""
        # Check for duplicate
        if self._is_duplicate(ref):
            self.metrics.references_deduplicated += 1
            logger.debug(f"Skipping duplicate reference: {ref}")
            return

        reference_data = {
            "book": ref.book,
            "chapter": ref.chapter,
            "verse_start": ref.verse_start,
            "verse_end": ref.verse_end,
            "confidence": ref.confidence,
            "inferred": ref.inferred,
            "text": "",
        }

        # Retry with exponential backoff
        for attempt in range(self.config.api_max_retries + 1):
            try:
                start_time = time.time()

                response = requests.post(
                    f"{self.server_url}/api/reference",
                    json=reference_data,
                    timeout=self.config.api_timeout,
                )

                duration_ms = (time.time() - start_time) * 1000
                self.metrics.record_api_duration(duration_ms)

                if response.status_code == 200:
                    self.metrics.references_sent += 1
                    logger.debug(f"Reference sent successfully: {ref}")
                    return
                elif response.status_code >= 500:
                    # Server error - retry
                    logger.warning(
                        f"Server error {response.status_code}, attempt {attempt + 1}/{self.config.api_max_retries + 1}"
                    )
                    if attempt < self.config.api_max_retries:
                        self.metrics.api_retries += 1
                        delay = min(
                            self.config.api_retry_base_delay * (2**attempt)
                            + random.uniform(0, 0.1),
                            self.config.api_retry_max_delay,
                        )
                        time.sleep(delay)
                        continue
                else:
                    # Client error - don't retry
                    logger.warning(
                        f"Failed to send reference {ref}: {response.status_code} - {response.text}"
                    )
                    self.metrics.references_failed += 1
                    return

            except requests.exceptions.Timeout:
                logger.warning(
                    f"Timeout sending reference, attempt {attempt + 1}/{self.config.api_max_retries + 1}"
                )
                if attempt < self.config.api_max_retries:
                    self.metrics.api_retries += 1
                    delay = min(
                        self.config.api_retry_base_delay * (2**attempt) + random.uniform(0, 0.1),
                        self.config.api_retry_max_delay,
                    )
                    time.sleep(delay)
                    continue
            except requests.exceptions.ConnectionError as e:
                logger.warning(
                    f"Connection error: {e}, attempt {attempt + 1}/{self.config.api_max_retries + 1}"
                )
                if attempt < self.config.api_max_retries:
                    self.metrics.api_retries += 1
                    delay = min(
                        self.config.api_retry_base_delay * (2**attempt) + random.uniform(0, 0.1),
                        self.config.api_retry_max_delay,
                    )
                    time.sleep(delay)
                    continue
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error sending reference: {e}")
                self.metrics.references_failed += 1
                return
            except Exception as e:
                logger.error(f"Unexpected error sending reference: {e}", exc_info=True)
                self.metrics.references_failed += 1
                return

        # All retries exhausted
        self.metrics.references_failed += 1
        logger.error(
            f"Failed to send reference after {self.config.api_max_retries + 1} attempts: {ref}"
        )


def main():
    """Main function to run the BibleLive audio pipeline"""
    import argparse

    parser = argparse.ArgumentParser(description="BibleLive Audio Processing Pipeline")
    parser.add_argument(
        "--bible-db", default="bible_data/biblelive.sqlite", help="Path to Bible SQLite database"
    )
    parser.add_argument("--kjv", default="data/kjv.json", help="Path to KJV JSON file")
    parser.add_argument("--server", default="http://localhost:8000", help="BibleLive server URL")
    parser.add_argument(
        "--model",
        default="tiny",
        choices=["tiny", "base", "small", "medium", "large-v2"],
        help="Whisper model size",
    )
    parser.add_argument(
        "--device", default="cpu", choices=["cpu", "cuda"], help="Device for Whisper model"
    )
    parser.add_argument(
        "--vad-aggressiveness",
        type=int,
        default=2,
        choices=[0, 1, 2, 3],
        help="WebRTC VAD aggressiveness (0=least, 3=most aggressive)",
    )

    args = parser.parse_args()

    # Create and start audio processor
    processor = BibleLiveAudioProcessor(
        bible_db_path=args.bible_db, kjv_path=args.kjv, server_url=args.server
    )

    # Override STT model if specified
    if args.model != "tiny" or args.device != "cpu":
        processor.stt = SpeechToText(model_size=args.model, device=args.device)

    # Override VAD aggressiveness if specified
    if args.vad_aggressiveness != 2:
        processor.vad = VoiceActivityDetector(aggressiveness=args.vad_aggressiveness)

    try:
        processor.start()

        # Keep running until interrupted
        print("\nBibleLive Audio Pipeline is running...")
        print("Speak into your microphone to detect Bible references")
        print("Press Ctrl+C to stop\n")

        while processor.is_running:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nStopping audio pipeline...")
    except Exception as e:
        logger.error(f"Error in audio pipeline: {e}")
    finally:
        processor.stop()


if __name__ == "__main__":
    main()
