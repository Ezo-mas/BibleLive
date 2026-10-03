"""Microphone audio capture via ffmpeg."""

import logging
import subprocess
import sys
import threading
from queue import Empty, Full, Queue
from typing import Optional

logger = logging.getLogger(__name__)


class AudioCapture:
    """Handles audio capture using ffmpeg with proper stderr handling and bounded queues."""

    def __init__(self, sample_rate: int = 16000, queue_maxsize: int = 1000):
        self.sample_rate = sample_rate
        self.audio_queue = Queue(maxsize=queue_maxsize)
        self.is_running = False
        self.ffmpeg_process = None
        self.capture_thread = None
        self.stderr_thread = None

        # Carry-over buffer for ffmpeg chunks that don't align with the frame
        # size requested by get_audio_frame(). Without this, any bytes past the
        # end of a frame are silently discarded and the VAD/STT lose audio.
        self._frame_buffer = bytearray()

        # Byte/second accounting so "is audio actually arriving?" is visible
        self.total_bytes_captured = 0
        self.total_chunks = 0
        self.dropped_chunks = 0
        self._last_progress_log = 0.0

    @property
    def captured_seconds(self) -> float:
        """Total audio seconds received from the input device."""
        return self.total_bytes_captured / 2 / self.sample_rate

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

                    self.total_bytes_captured += len(raw_data)
                    self.total_chunks += 1
                    if self.total_chunks == 1:
                        logger.info(
                            "First audio input received from device "
                            f"({len(raw_data)} bytes, {self.sample_rate}Hz mono s16le)"
                        )

                    # Use non-blocking put with timeout to handle backpressure
                    try:
                        self.audio_queue.put(raw_data, timeout=0.1)
                    except Full:
                        self.dropped_chunks += 1
                        logger.warning(
                            "Audio queue full, dropping audio chunk "
                            f"({self.dropped_chunks} dropped so far)"
                        )

                    self._log_capture_progress()

                except OSError as e:
                    logger.error(f"OS error reading ffmpeg stdout: {e}")
                    break

        except Exception as e:
            logger.error(f"Unexpected error reading audio data: {e}", exc_info=True)
        finally:
            logger.info(
                f"Audio capture thread stopped "
                f"({self.captured_seconds:.1f}s of audio captured, "
                f"{self.total_chunks} chunks, {self.dropped_chunks} dropped)"
            )

    def _log_capture_progress(self) -> None:
        """Log a live 'audio is flowing' line roughly every 10 seconds of audio."""
        if self.captured_seconds - self._last_progress_log < 10.0:
            return
        self._last_progress_log = self.captured_seconds
        logger.info(
            f"Audio input active: {self.captured_seconds:.0f}s captured "
            f"({self.total_chunks} chunks, queue={self.audio_queue.qsize()}, "
            f"dropped={self.dropped_chunks})"
        )

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

        # Fill the carry-over buffer until we have at least one full frame.
        # ffmpeg writes chunks of arbitrary size, so bytes past the end of a
        # frame are kept here for the next call instead of being discarded
        # (previously the excess was silently dropped, losing VAD/STT audio).
        while len(self._frame_buffer) < bytes_per_frame and self.is_running:
            try:
                self._frame_buffer.extend(self.audio_queue.get(timeout=0.1))
            except Empty:
                if not self.is_running:
                    break
                continue

        if len(self._frame_buffer) >= bytes_per_frame:
            frame = bytes(self._frame_buffer[:bytes_per_frame])
            del self._frame_buffer[:bytes_per_frame]
            return frame
        return None
