"""Microphone audio capture via ffmpeg."""

import logging
import re
import subprocess
import sys
import threading
import time
from collections import deque
from queue import Empty, Full, Queue

logger = logging.getLogger(__name__)

FRAME_WAIT_S = 0.5

# ``ffmpeg -list_devices true -f dshow`` prints each device as
# ``"Microphone (Realtek(R) Audio)" (audio)``; the alternative-name lines that
# follow carry no ``(audio)`` suffix.
_DSHOW_AUDIO_DEVICE = re.compile(r'"(?P<name>[^"]+)"\s+\(audio\)')
# ``ffmpeg -f avfoundation -list_devices true -i ""`` prints video devices, then
# ``AVFoundation audio devices:`` and one ``[index] name`` line per audio input.
_AVFOUNDATION_DEVICE = re.compile(r"\]\s\[(?P<index>\d+)\]\s(?P<name>.+)$")


# ffmpeg is a console program. Started from a process with no console (the
# launcher runs the pipeline and the GUI under pythonw.exe), Windows would give
# it a new, empty console window; CREATE_NO_WINDOW keeps it headless.
def _no_window_flags() -> int:
    """``creationflags`` that keep a console program from opening a window."""
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def list_input_devices() -> list[str]:
    """Names of the audio inputs ffmpeg can open: DirectShow (Windows) or AVFoundation (macOS).

    dshow has no ``default`` device: ``-i audio=<name>`` needs one of these
    names verbatim. On macOS a name works the same way (``-i :<name>``), and
    no name means the system's default input. Returns an empty list on other
    platforms.
    """
    if sys.platform == "darwin":
        return _list_avfoundation_audio()
    if sys.platform != "win32":
        return []
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=_no_window_flags(),
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Please install ffmpeg and ensure it's in PATH")
    # ffmpeg exits non-zero here (the "dummy" input never opens); the list is on stderr.
    return _DSHOW_AUDIO_DEVICE.findall(result.stderr)


def _list_avfoundation_audio() -> list[str]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Please install ffmpeg and ensure it's in PATH")
    names: list[str] = []
    in_audio = False
    for line in result.stderr.splitlines():
        if "AVFoundation audio devices:" in line:
            in_audio = True
        elif "AVFoundation video devices:" in line:
            in_audio = False
        elif in_audio and (match := _AVFOUNDATION_DEVICE.search(line)):
            names.append(match.group("name").strip())
    return names


class AudioCapture:
    """Handles audio capture using ffmpeg with proper stderr handling and bounded queues."""

    def __init__(self, sample_rate: int = 16000, queue_maxsize: int = 1000):
        self.sample_rate = sample_rate
        self.audio_queue: Queue[bytes] = Queue(maxsize=queue_maxsize)
        self.is_running = False
        self.ffmpeg_process: subprocess.Popen[bytes] | None = None
        self.capture_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None
        # Lifecycle thread invalidates the stream under this lock; readers use it
        # to publish data and failure only while their stream is current.
        self._stream_lock = threading.Lock()
        self._stream_generation = 0

        # Carry-over buffer for ffmpeg chunks that don't align with the frame
        # size requested by get_audio_frame(). Without this, any bytes past the
        # end of a frame are silently discarded and the VAD/STT lose audio.
        self._frame_buffer = bytearray()

        # Byte/second accounting so "is audio actually arriving?" is visible
        self.total_bytes_captured = 0
        self.total_chunks = 0
        self.dropped_chunks = 0
        self._last_progress_log = 0.0

        # Recent ffmpeg stderr, so an early exit (bad device, no mic) is
        # reported at ERROR instead of being visible only at DEBUG.
        self._stderr_tail: deque[str] = deque(maxlen=5)

        # Set when the audio stream ends without stop_capture() asking it to
        # (ffmpeg exited: device unplugged, driver reset, device taken). The
        # owner polls it to restart capture; get_audio_frame() stops waiting.
        self.failed = False

    @property
    def captured_seconds(self) -> float:
        """Total audio seconds received from the input device."""
        return self.total_bytes_captured / 2 / self.sample_rate

    def start_capture(self, device: str | None = None) -> None:
        """Start capturing audio from the microphone.

        ``device`` is the platform's input name: a DirectShow device name on
        Windows, a PulseAudio source on Linux, an avfoundation index or name on
        macOS. ``None`` picks the platform default (the system default input on
        macOS; the first dshow device on Windows, which has no default of its own).
        """
        # Build ffmpeg command
        if sys.platform == "darwin":  # macOS
            # ":" alone names no device at all ("No AV capture device found"), so the
            # microphone is never opened and macOS never asks for permission.
            input_args = ["-f", "avfoundation", "-i", f":{device or 'default'}"]
        elif sys.platform.startswith("linux"):  # Linux
            input_args = ["-f", "pulse", "-i", device or "default"]
        elif sys.platform == "win32":  # Windows
            input_args = ["-f", "dshow", "-i", f"audio={device or self._default_dshow_device()}"]
        else:
            raise ValueError(f"Unsupported platform: {sys.platform}")

        # A restart begins a new stream: a partial frame left over from the dead
        # one is not contiguous with the new audio, so it is discarded here (and
        # only here; between frames of one stream carry-over is always kept).
        self._frame_buffer.clear()
        while True:
            try:
                self.audio_queue.get_nowait()
            except Empty:
                break
        with self._stream_lock:
            self._stream_generation += 1
            generation = self._stream_generation
            self.failed = False
            self.is_running = True

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
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=_no_window_flags(),
            )
        except FileNotFoundError:
            self.is_running = False
            raise RuntimeError("ffmpeg not found. Please install ffmpeg and ensure it's in PATH")
        except (OSError, subprocess.SubprocessError) as e:
            self.is_running = False
            raise RuntimeError(f"Failed to start ffmpeg: {e}")

        # Start thread to consume stderr (prevents blocking)
        stderr_thread = threading.Thread(
            target=self._consume_stderr, daemon=True, name="ffmpeg-stderr"
        )
        self.stderr_thread = stderr_thread
        stderr_thread.start()

        # Start thread to read audio data
        capture_thread = threading.Thread(
            target=self._read_audio_data,
            args=(self.ffmpeg_process, generation),
            daemon=True,
            name="audio-capture",
        )
        self.capture_thread = capture_thread
        capture_thread.start()

    def _consume_stderr(self) -> None:
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
                    self._stderr_tail.append(line_str)
                    logger.debug(f"ffmpeg: {line_str}")
        except OSError as e:
            # Pipe closed, expected during shutdown
            logger.debug(f"ffmpeg stderr pipe closed: {e}")
        except Exception as e:
            logger.warning(f"Error consuming ffmpeg stderr: {e}")

    def _read_audio_data(self, proc: subprocess.Popen[bytes], generation: int) -> None:
        """Read audio data from ffmpeg stdout and put in queue"""
        chunk_size = 2 * self.sample_rate // 100  # 10ms chunks (2 bytes per sample)

        try:
            while True:
                if proc.stdout is None:
                    break
                try:
                    raw_data = proc.stdout.read(chunk_size)
                    with self._stream_lock:
                        if not self.is_running or generation != self._stream_generation:
                            break
                        if not raw_data:
                            break
                        self.total_bytes_captured += len(raw_data)
                        self.total_chunks += 1
                        if self.total_chunks == 1:
                            logger.info(
                                "First audio input received from device "
                                f"({len(raw_data)} bytes, {self.sample_rate}Hz mono s16le)"
                            )
                    deadline = time.monotonic() + 0.1
                    active = True
                    while True:
                        with self._stream_lock:
                            if not self.is_running or generation != self._stream_generation:
                                active = False
                                break
                            try:
                                self.audio_queue.put_nowait(raw_data)
                                break
                            except Full:
                                if time.monotonic() >= deadline:
                                    self.dropped_chunks += 1
                                    logger.warning(
                                        "Audio queue full, dropping audio chunk "
                                        f"({self.dropped_chunks} dropped so far)"
                                    )
                                    break
                        time.sleep(0.005)
                    if not active:
                        break

                    self._log_capture_progress()

                except OSError as e:
                    logger.error(f"OS error reading ffmpeg stdout: {e}")
                    break

        except Exception as e:
            logger.error(f"Unexpected error reading audio data: {e}", exc_info=True)
        finally:
            # Leaving the read loop while capture is still meant to run means no
            # audio will ever arrive on this stream again.
            with self._stream_lock:
                current = self.is_running and generation == self._stream_generation
                if current:
                    self.failed = True
            if current:
                self._report_ffmpeg_exit()
            logger.info(
                f"Audio capture thread stopped "
                f"({self.captured_seconds:.1f}s of audio captured, "
                f"{self.total_chunks} chunks, {self.dropped_chunks} dropped)"
            )

    @staticmethod
    def _default_dshow_device() -> str:
        """First DirectShow audio input; dshow rejects ``audio=default``."""
        devices = list_input_devices()
        if not devices:
            raise RuntimeError(
                "No DirectShow audio input found. Connect a microphone, or check "
                "`ffmpeg -list_devices true -f dshow -i dummy`."
            )
        logger.warning(
            f"No --input-device given; using the first audio input {devices[0]!r}. "
            f"Available: {devices}. Pass --input-device to choose another."
        )
        return devices[0]

    def _report_ffmpeg_exit(self) -> None:
        """Say why capture ended: a stop request, or ffmpeg dying on its own."""
        if not self.is_running:
            logger.info("ffmpeg stdout closed, stopping audio capture")
            return
        # Let the stderr thread drain ffmpeg's last lines before reporting them.
        if self.stderr_thread is not None:
            self.stderr_thread.join(timeout=1.0)
        detail = " | ".join(self._stderr_tail) or "no ffmpeg output"
        logger.error(
            f"ffmpeg exited after {self.captured_seconds:.1f}s of audio; capture stopped. "
            f"ffmpeg said: {detail}"
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

    def stop_capture(self) -> None:
        """Stop audio capture and cleanup resources"""
        logger.info("Stopping audio capture...")
        with self._stream_lock:
            self.is_running = False
            self._stream_generation += 1

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

    def get_audio_frame(self, frame_duration_ms: int = 30) -> bytes | None:
        """Get audio frame of specified duration.

        Returns ``None`` after a bounded wait, on stop, or when a failed stream
        has been drained. Partial frames remain buffered across calls.
        """
        # Calculate bytes needed for frame duration
        bytes_per_sample = 2  # 16-bit
        samples_per_frame = int(self.sample_rate * frame_duration_ms / 1000)
        bytes_per_frame = samples_per_frame * bytes_per_sample

        # Fill the carry-over buffer until we have at least one full frame.
        # ffmpeg writes chunks of arbitrary size, so bytes past the end of a
        # frame are kept here for the next call instead of being discarded
        # (previously the excess was silently dropped, losing VAD/STT audio).
        deadline = time.monotonic() + FRAME_WAIT_S
        while len(self._frame_buffer) < bytes_per_frame and self.is_running:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._frame_buffer.extend(self.audio_queue.get(timeout=min(0.1, remaining)))
            except Empty:
                if not self.is_running or self.failed:
                    break
                continue

        if len(self._frame_buffer) >= bytes_per_frame:
            frame = bytes(self._frame_buffer[:bytes_per_frame])
            del self._frame_buffer[:bytes_per_frame]
            return frame
        return None
