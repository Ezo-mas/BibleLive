"""Recovery when ffmpeg exits on its own (microphone unplugged, driver reset).

Before the fix, capture kept ``is_running=True`` after ffmpeg died, so
``get_audio_frame()`` waited forever, the VAD thread hung, and detection was
gone for the rest of the service while the process looked healthy. No real
ffmpeg, device, model or GPU is needed: ``subprocess.Popen`` is faked.
"""

import io
import threading
import time
from queue import Queue

import pytest

from audio import capture as capture_module
from audio.capture import AudioCapture
from audio.config import AudioPipelineConfig
from audio.metrics import PipelineMetrics
from audio.pipeline import BibleLiveAudioProcessor
from audio.segmenter import Segmenter

# The pipeline module imports faster-whisper (optional audio extras).
pytestmark = pytest.mark.audio

FRAME_BYTES = 960  # 30 ms of 16 kHz 16-bit mono


class _ExitingFfmpeg:
    """ffmpeg that writes ``audio`` and then exits, as when a device disappears."""

    def __init__(self, audio: bytes):
        self.stdout = io.BytesIO(audio)
        self.stderr = io.BytesIO(b"audio device lost\n")

    def poll(self):
        return 1

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 1

    def kill(self):
        pass


class _StreamingFfmpeg:
    """ffmpeg that streams silence until it is terminated."""

    def __init__(self):
        self._stopped = threading.Event()
        self.stdout = self
        self.stderr = io.BytesIO(b"")

    def read(self, size):
        if self._stopped.wait(0.002):
            return b""
        return b"\0" * size

    def poll(self):
        return 0 if self._stopped.is_set() else None

    def terminate(self):
        self._stopped.set()

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._stopped.set()


def _frame_within(capture: AudioCapture, seconds: float = 1.0):
    """``get_audio_frame(30)``, failing the test instead of hanging the suite.

    The defect under test is exactly a call that never returns, so it is made
    on a worker thread with a deadline.
    """
    result: list = []
    worker = threading.Thread(target=lambda: result.append(capture.get_audio_frame(30)))
    worker.daemon = True
    worker.start()
    worker.join(timeout=seconds)
    if worker.is_alive():
        capture.is_running = False  # release the stuck call so the thread ends
        pytest.fail(f"get_audio_frame() still blocked after {seconds}s")
    return result[0]


def _fake_popen(monkeypatch, processes):
    """Make each ``Popen`` call return the next fake process."""
    monkeypatch.setattr(capture_module.sys, "platform", "linux")
    remaining = list(processes)

    def _popen(cmd, **kwargs):
        return remaining.pop(0)

    monkeypatch.setattr(capture_module.subprocess, "Popen", _popen)


# --- AudioCapture -----------------------------------------------------------


def test_unrequested_exit_marks_capture_failed_and_frames_stop_blocking(monkeypatch):
    _fake_popen(monkeypatch, [_ExitingFfmpeg(b"\0" * (FRAME_BYTES * 3 + 100))])
    capture = AudioCapture()
    capture.start_capture()
    capture.capture_thread.join(timeout=2)

    assert capture.failed is True

    # The audio that did arrive is still delivered, frame by frame ...
    frames = [_frame_within(capture) for _ in range(3)]
    assert all(frame is not None and len(frame) == FRAME_BYTES for frame in frames)

    # ... and then the call returns instead of waiting forever.
    assert _frame_within(capture) is None

    capture.stop_capture()


def test_requested_stop_is_not_a_failure(monkeypatch):
    _fake_popen(monkeypatch, [_StreamingFfmpeg()])
    capture = AudioCapture()
    capture.start_capture()
    assert capture.get_audio_frame(30) is not None

    capture.stop_capture()

    assert capture.failed is False
    assert capture.get_audio_frame(30) is None


def test_restart_starts_a_clean_stream(monkeypatch):
    """A partial frame and queued chunks from the dead stream are not reused."""
    _fake_popen(monkeypatch, [_ExitingFfmpeg(b"\x01" * 100), _StreamingFfmpeg()])
    capture = AudioCapture()
    capture.start_capture()
    capture.capture_thread.join(timeout=2)
    assert _frame_within(capture) is None  # 100 bytes: not a whole frame
    assert capture.failed is True

    capture.stop_capture()
    capture.start_capture()

    assert capture.failed is False
    assert capture.get_audio_frame(30) == b"\0" * FRAME_BYTES
    capture.stop_capture()


# --- Pipeline restart -------------------------------------------------------


class _FlakyCapture:
    """Capture whose restarts fail ``failures`` times before one succeeds."""

    def __init__(self, failures: int = 0):
        self.failed = True
        self.failures = failures
        self.captured_seconds = 0.0
        self.starts = 0
        self.stops = 0

    def stop_capture(self):
        self.stops += 1

    def start_capture(self, device=None):  # noqa: ARG002 - signature parity
        self.starts += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("no audio device")
        self.failed = False


def _processor(capture, **config) -> BibleLiveAudioProcessor:
    """A processor wired to ``capture`` without ffmpeg, a VAD model or Whisper."""
    proc = BibleLiveAudioProcessor.__new__(BibleLiveAudioProcessor)
    proc.config = AudioPipelineConfig(**config)
    proc.is_running = True
    proc.audio_capture = capture
    proc.segmenter = Segmenter(lambda frame: True)
    proc.vad_queue = Queue(maxsize=10)
    proc.metrics = PipelineMetrics()
    proc.frame_duration_ms = 30
    proc._frame_duration_s = 0.03
    proc._capture_language = "en"
    proc._pending_language = None
    proc._pending_language_lock = threading.Lock()
    proc._capture_lock = threading.Lock()
    proc._capture_restart_attempt = 0
    proc._capture_restarted_at_s = None
    proc._capture_down = False
    return proc


def test_restart_queues_the_utterance_in_progress_and_restarts_capture():
    proc = _processor(_FlakyCapture(), capture_restart_base_delay=0.0)
    for _ in range(20):  # 0.6 s of speech, cut off by the failure
        proc.segmenter.push(b"\0" * FRAME_BYTES)

    proc._restart_capture()

    language, frames = proc.vad_queue.get_nowait()
    assert (language, len(frames)) == ("en", 20)
    assert proc.audio_capture.failed is False
    assert proc.metrics.capture_failures == 1
    assert proc.metrics.capture_restarts == 1


def test_failed_restarts_back_off_until_one_succeeds(monkeypatch):
    capture = _FlakyCapture(failures=3)
    proc = _processor(capture, capture_restart_base_delay=1.0, capture_restart_max_delay=4.0)
    waits: list[float] = []
    monkeypatch.setattr(proc, "_wait_while_running", lambda s: waits.append(s) or True)

    proc._restart_capture()

    assert waits == [1.0, 2.0, 4.0, 4.0]  # doubling, capped
    assert capture.starts == 4
    assert proc.metrics.capture_restarts == 1


def test_stop_during_backoff_does_not_start_ffmpeg_again(monkeypatch):
    capture = _FlakyCapture()
    proc = _processor(capture, capture_restart_base_delay=5.0)

    def stop_while_waiting(_seconds):
        proc.is_running = False
        return False

    monkeypatch.setattr(proc, "_wait_while_running", stop_while_waiting)

    proc._restart_capture()

    assert capture.starts == 0


def test_backoff_resets_once_the_restarted_stream_is_healthy():
    capture = _FlakyCapture()
    proc = _processor(capture, capture_healthy_s=10.0)
    proc._capture_restart_attempt = 3
    proc._capture_restarted_at_s = 0.0

    capture.captured_seconds = 9.0
    proc._note_capture_healthy()
    assert proc._capture_restart_attempt == 3

    capture.captured_seconds = 10.0
    proc._note_capture_healthy()
    assert proc._capture_restart_attempt == 0


def test_vad_loop_resumes_after_ffmpeg_dies(monkeypatch):
    """End to end through the real VAD loop and AudioCapture: the old hang."""
    _fake_popen(monkeypatch, [_ExitingFfmpeg(b"\0" * FRAME_BYTES * 5), _StreamingFfmpeg()])
    capture = AudioCapture()
    proc = _processor(capture, capture_restart_base_delay=0.0)
    proc.segmenter = Segmenter(lambda frame: False)  # silence: no utterances
    proc.last_metrics_log = time.time()
    capture.start_capture()

    vad_thread = threading.Thread(target=proc._vad_loop, daemon=True)
    vad_thread.start()
    try:
        deadline = time.monotonic() + 5
        while proc.metrics.audio_chunks_captured < 50 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        proc.is_running = False
        with proc._capture_lock:
            capture.stop_capture()
        vad_thread.join(timeout=2)

    assert proc.metrics.capture_failures == 1
    assert proc.metrics.capture_restarts == 1
    assert proc.metrics.audio_chunks_captured >= 50  # frames flowed again after the restart
    assert not vad_thread.is_alive()


def test_operator_hears_about_the_outage_until_capture_is_back(monkeypatch):
    """The heartbeat reports capture as down for exactly the restart window."""
    proc = _processor(_FlakyCapture(failures=1), capture_restart_base_delay=0.0)
    down_while_waiting: list[bool] = []
    monkeypatch.setattr(
        proc, "_wait_while_running", lambda s: down_while_waiting.append(proc._capture_down) or True
    )

    proc._restart_capture()

    assert down_while_waiting == [True, True]
    assert proc._capture_down is False
