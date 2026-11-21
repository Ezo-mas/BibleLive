"""Recovery when ffmpeg exits on its own (microphone unplugged, driver reset).

Before the fix, capture kept ``is_running=True`` after ffmpeg died, so
``get_audio_frame()`` waited forever, the VAD thread hung, and detection was
gone for the rest of the service while the process looked healthy. No real
ffmpeg, device, model or GPU is needed: ``subprocess.Popen`` is faked.
"""

import io
import json
import threading
import time
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from processor_factory import make_processor

from audio import capture as capture_module
from audio.capture import AudioCapture
from audio.config import AudioPipelineConfig
from audio.pipeline import BibleLiveAudioProcessor
from audio.segmenter import Segmenter
from audio.session_log import SessionLog

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
    return make_processor(
        config=AudioPipelineConfig(**config),
        is_running=True,
        audio_capture=capture,
        segmenter=Segmenter(lambda frame: True),
        vad_queue=Queue(maxsize=10),
        _partial_schedule=None,  # partial transcripts are not under test here
    )


def test_restart_queues_the_utterance_in_progress_and_restarts_capture():
    proc = _processor(_FlakyCapture(), capture_restart_base_delay=0.0)
    meter = Mock()
    proc.level_sender = meter
    for _ in range(20):  # 0.6 s of speech, cut off by the failure
        proc.segmenter.push(b"\0" * FRAME_BYTES)

    proc._restart_capture()

    language, frames = proc.vad_queue.get_nowait()
    assert (language, len(frames)) == ("en", 20)
    assert proc.audio_capture.failed is False
    assert proc.metrics.capture_failures == 1
    assert proc.metrics.capture_restarts == 1
    meter.reset_capture.assert_called_once_with()


def test_silence_clock_restarts_with_the_stream():
    """Silence during an outage is not silence from the restarted microphone."""
    proc = _processor(_FlakyCapture(), capture_restart_base_delay=0.0)
    proc.capture_supervisor.last_loud_at = time.monotonic() - 600
    proc.capture_supervisor.mic_silent_logged = True

    proc._restart_capture()

    assert time.monotonic() - proc.capture_supervisor.last_loud_at < 5
    assert proc.capture_supervisor.mic_silent_logged is False


def test_failed_restarts_back_off_until_one_succeeds(monkeypatch):
    capture = _FlakyCapture(failures=3)
    proc = _processor(capture, capture_restart_base_delay=1.0, capture_restart_max_delay=4.0)
    waits: list[float] = []
    monkeypatch.setattr(
        proc.capture_supervisor, "wait_while_running", lambda s: waits.append(s) or True
    )

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

    monkeypatch.setattr(proc.capture_supervisor, "wait_while_running", stop_while_waiting)

    proc._restart_capture()

    assert capture.starts == 0


def test_backoff_resets_once_the_restarted_stream_is_healthy():
    capture = _FlakyCapture()
    proc = _processor(capture, capture_healthy_s=10.0)
    proc.capture_supervisor.restart_attempt = 3
    proc.capture_supervisor.restarted_at_s = 0.0

    capture.captured_seconds = 9.0
    proc.capture_supervisor.note_capture_healthy()
    assert proc.capture_supervisor.restart_attempt == 3

    capture.captured_seconds = 10.0
    proc.capture_supervisor.note_capture_healthy()
    assert proc.capture_supervisor.restart_attempt == 0


def test_vad_loop_resumes_after_ffmpeg_dies(monkeypatch):
    """End to end through the real VAD loop and AudioCapture: the old hang."""
    monkeypatch.setattr(capture_module, "FRAME_WAIT_S", 0.05)
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
        with proc.capture_supervisor.lock:
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
        proc.capture_supervisor,
        "wait_while_running",
        lambda s: down_while_waiting.append(proc.capture_supervisor.down) or True,
    )

    proc._restart_capture()

    assert down_while_waiting == [True, True]
    assert proc.capture_supervisor.down is True


def test_partial_frame_survives_bounded_wait(monkeypatch):
    monkeypatch.setattr(capture_module, "FRAME_WAIT_S", 0.05)
    capture = AudioCapture()
    capture.is_running = True
    data = bytes(i % 251 for i in range(FRAME_BYTES))
    capture.audio_queue.put(data[:300])
    assert capture.get_audio_frame() is None
    capture.audio_queue.put(data[300:])
    assert capture.get_audio_frame() == data
    assert not capture._frame_buffer


class _HungFfmpeg(_StreamingFfmpeg):
    """The capture reader waits until the VAD thread signals termination."""

    def read(self, size):
        self._stopped.wait()
        return b""


def test_hung_capture_restarts_without_health_flapping(monkeypatch):
    monkeypatch.setattr(capture_module, "FRAME_WAIT_S", 0.05)
    first, second = _HungFfmpeg(), _HungFfmpeg()
    _fake_popen(monkeypatch, [first, second, _StreamingFfmpeg()])
    capture = AudioCapture()
    proc = _processor(
        capture,
        capture_restart_base_delay=0.0,
        capture_stall_restart_s=0.3,
        capture_first_frame_grace_s=0.8,
    )
    proc.sender = SimpleNamespace(delivery_problem=None)
    proc.stt = SimpleNamespace(language="en")
    proc.last_metrics_log = time.time()
    capture.start_capture()
    thread = threading.Thread(target=proc._vad_loop)
    thread.start()
    try:
        deadline = time.monotonic() + 8
        while proc.metrics.capture_restarts < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert proc.metrics.capture_restarts == 1
        assert [i["code"] for i in proc.health_report()["issues"]] == ["capture_stopped"]
        time.sleep(0.1)
        assert [i["code"] for i in proc.health_report()["issues"]] == ["capture_stopped"]
        while proc.metrics.audio_chunks_captured == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert proc.metrics.capture_restarts == 2
        assert proc.metrics.audio_chunks_captured > 0
        assert proc.health_report()["issues"] == []
    finally:
        proc.is_running = False
        with proc.capture_supervisor.lock:
            capture.stop_capture()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_vad_loop_recovers_from_exception_and_stops(monkeypatch):
    capture = _FlakyCapture()
    capture.failed = False
    proc = _processor(capture)
    calls = []
    recovered = threading.Event()
    release = threading.Event()

    def read(_duration):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("bad frame")
        recovered.set()
        release.wait(2)
        return None

    capture.get_audio_frame = read
    proc.sender = SimpleNamespace(delivery_problem=None)
    proc.stt = SimpleNamespace(language="en")
    thread = threading.Thread(target=proc._vad_loop)
    proc.vad_thread = thread
    thread.start()
    assert recovered.wait(2)
    assert thread.is_alive()
    assert not any(issue["code"] == "thread_stopped" for issue in proc.health_report()["issues"])
    proc.is_running = False
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(calls) == 2


def test_utterance_failures_report_and_clear_while_thread_runs(monkeypatch):
    proc = _processor(_FlakyCapture())
    proc.sender = SimpleNamespace(delivery_problem=None)
    proc.stt = SimpleNamespace(language="en")
    monkeypatch.setattr(proc, "_init_thread_local_parsers", lambda: None)
    results = iter([False, False, False, True])
    processed = threading.Event()

    def detect(_language, _frames):
        if not next(results):
            raise RuntimeError("unexpected parser failure")
        processed.set()
        return None, []

    monkeypatch.setattr(proc, "_detect_references", detect)
    thread = threading.Thread(target=proc._transcription_loop)
    thread.start()
    try:
        for _ in range(3):
            proc.vad_queue.put(("en", []))
        deadline = time.monotonic() + 2
        while proc._consecutive_utterance_failures < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        issues = proc.health_report()["issues"]
        assert {
            "code": "utterance_failed",
            "params": {"count": 3},
            "text": "Processing failed on the last 3 utterances; they were skipped",
        } in issues
        assert thread.is_alive()
        proc.vad_queue.put(("en", []))
        assert processed.wait(2)
        deadline = time.monotonic() + 2
        while proc._consecutive_utterance_failures and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(i["code"] == "utterance_failed" for i in proc.health_report()["issues"])
        assert thread.is_alive()
    finally:
        proc.is_running = False
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_language_switch_applies_while_capture_is_hung(monkeypatch):
    monkeypatch.setattr(capture_module, "FRAME_WAIT_S", 0.05)
    _fake_popen(monkeypatch, [_HungFfmpeg()])
    capture = AudioCapture()
    proc = _processor(capture, capture_stall_restart_s=10.0)
    capture.start_capture()
    thread = threading.Thread(target=proc._vad_loop)
    thread.start()
    try:
        with proc._pending_language_lock:
            proc._pending_language = "lt"
        deadline = time.monotonic() + 2
        while proc._capture_language != "lt" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert proc._capture_language == "lt"
        assert proc.metrics.capture_restarts == 0
    finally:
        proc.is_running = False
        capture.stop_capture()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_old_reader_cannot_publish_after_restart(monkeypatch):
    class LateReader(_StreamingFfmpeg):
        """The test releases the old reader after the lifecycle thread restarts capture."""

        def __init__(self):
            super().__init__()
            self.release = threading.Event()
            self.entered = threading.Event()

        def read(self, size):
            self.entered.set()
            self.release.wait()
            return b"x" * size

        def terminate(self):
            pass

    old = LateReader()
    new = _StreamingFfmpeg()
    _fake_popen(monkeypatch, [old, new])
    capture = AudioCapture()
    capture.start_capture()
    assert old.entered.wait(2)
    old_thread = capture.capture_thread
    capture.stop_capture()  # reader survives the bounded join
    assert old_thread.is_alive()
    capture.start_capture()
    try:
        old.release.set()
        old_thread.join(2)
        assert not old_thread.is_alive()
        assert capture.get_audio_frame() == b"\0" * FRAME_BYTES
        assert not capture.failed
    finally:
        old.release.set()
        capture.stop_capture()


def test_vad_persistent_errors_stop_thread_and_report_health(monkeypatch):
    capture = _FlakyCapture()
    capture.failed = False
    capture.get_audio_frame = lambda _duration: b"\0" * FRAME_BYTES
    capture.audio_queue = Queue()
    proc = _processor(capture, partial_transcripts=False)
    proc.sender = SimpleNamespace(delivery_problem=None)
    proc.stt = SimpleNamespace(language="en")
    proc.segmenter = SimpleNamespace(
        push=lambda _frame: (_ for _ in ()).throw(ValueError("bad VAD"))
    )
    monkeypatch.setattr(proc.capture_supervisor, "wait_while_running", lambda _seconds: True)
    failures = []

    def run():
        try:
            proc._vad_loop()
        except ValueError as exc:
            failures.append(str(exc))

    thread = threading.Thread(target=run)
    proc.vad_thread = thread
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert failures == ["bad VAD"]
    assert any(issue["code"] == "thread_stopped" for issue in proc.health_report()["issues"])
    proc.is_running = False


def test_utterance_error_logging_is_guarded_and_not_counted_after_detection(monkeypatch):
    proc = _processor(_FlakyCapture())
    events = []
    proc.session_log = SimpleNamespace(
        utterance_failed=lambda frames, language: events.append((frames, language))
    )

    def fail(*args):
        raise RuntimeError("broken")

    monkeypatch.setattr(proc, "_detect_references", fail)
    proc._process_utterance("en", [])
    assert events == [([], "en")]
    assert proc._consecutive_utterance_failures == 1
    proc.session_log.utterance_failed = fail
    proc._process_utterance("en", [])
    assert proc._consecutive_utterance_failures == 2
    monkeypatch.setattr(proc, "_detect_references", lambda *_args: (None, []))
    proc.session_log.utterance = fail
    proc._process_utterance("en", [])
    assert proc._consecutive_utterance_failures == 0


def test_first_frame_grace_then_normal_stall_and_restart_timer(monkeypatch):
    monkeypatch.setattr(capture_module, "FRAME_WAIT_S", 0.02)
    first, second = _HungFfmpeg(), _HungFfmpeg()
    _fake_popen(monkeypatch, [first, second, _StreamingFfmpeg()])
    capture = AudioCapture()
    proc = _processor(
        capture,
        capture_restart_base_delay=0.0,
        capture_stall_restart_s=0.15,
        capture_first_frame_grace_s=0.7,
    )
    capture.start_capture()
    thread = threading.Thread(target=proc._vad_loop)
    thread.start()
    try:
        time.sleep(0.3)
        assert proc.metrics.capture_restarts == 0  # first-frame grace exceeds normal timeout
        capture.audio_queue.put(b"a" * FRAME_BYTES)
        deadline = time.monotonic() + 3
        while proc.metrics.capture_restarts < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert proc.metrics.capture_restarts == 1  # normal timeout after a frame
        time.sleep(0.3)
        assert proc.metrics.capture_restarts == 1  # fresh first-frame grace after restart
        while proc.metrics.capture_restarts < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert proc.metrics.capture_restarts == 2
    finally:
        proc.is_running = False
        with proc.capture_supervisor.lock:
            capture.stop_capture()
        thread.join(2)
    assert not thread.is_alive()


def test_repeated_stall_restart_backoff_grows_and_discards_buffer(monkeypatch):
    monkeypatch.setattr(capture_module, "FRAME_WAIT_S", 0.02)
    first, second, third = _HungFfmpeg(), _HungFfmpeg(), _StreamingFfmpeg()
    _fake_popen(monkeypatch, [first, second, third])
    capture = AudioCapture()
    proc = _processor(
        capture,
        capture_restart_base_delay=1.0,
        capture_restart_max_delay=4.0,
        capture_first_frame_grace_s=0.2,
    )
    waits = []
    monkeypatch.setattr(
        proc.capture_supervisor, "wait_while_running", lambda seconds: waits.append(seconds) or True
    )
    capture.start_capture()
    capture.audio_queue.put(b"x" * 100)
    frames = []
    proc.segmenter = Segmenter(lambda frame: frames.append(frame) or False)
    thread = threading.Thread(target=proc._vad_loop)
    thread.start()
    try:
        deadline = time.monotonic() + 4
        while proc.metrics.audio_chunks_captured == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert proc.metrics.capture_restarts == 2
        assert waits[:2] == [1.0, 2.0]
        assert frames and frames[0] == b"\0" * FRAME_BYTES
        assert b"x" not in capture._frame_buffer
    finally:
        proc.is_running = False
        with proc.capture_supervisor.lock:
            capture.stop_capture()
        thread.join(2)
    assert not thread.is_alive()


def test_failed_utterance_logs_position_and_removes_pending(tmp_path, monkeypatch):
    log = SessionLog(tmp_path, False, {}, 0.03)
    frames = [b"\0" * FRAME_BYTES for _ in range(4)]
    for frame in frames:
        log.write_frame(frame)
    log.speech_started(4)
    log.utterance_queued(frames, 0)
    proc = _processor(_FlakyCapture())
    proc.session_log = log

    def fail(_language, _frames):
        raise RuntimeError("detection failed")

    monkeypatch.setattr(proc, "_detect_references", fail)
    try:
        proc._process_utterance("en", frames)
        assert id(frames) not in log._pending
    finally:
        log.close()
    records = [
        json.loads(line) for line in log.transcript_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["type"] == "utterance_failed"
    assert records[-1]["language"] == "en"
    assert (records[-1]["start_s"], records[-1]["end_s"]) == (0.0, 0.12)


def test_raising_restart_resets_first_frame_grace(monkeypatch):
    capture = _FlakyCapture()
    capture.failed = False
    capture.audio_queue = Queue()
    frames = iter([b"\0" * FRAME_BYTES])
    capture.get_audio_frame = lambda _duration: next(frames, None)
    proc = _processor(
        capture,
        partial_transcripts=False,
        capture_stall_restart_s=0.1,
        capture_first_frame_grace_s=0.8,
    )
    proc.segmenter = Segmenter(lambda _frame: False)
    restarts = []
    raised = threading.Event()

    def restart(_cause):
        restarts.append(time.monotonic())
        raised.set()
        raise RuntimeError("restart failed")

    monkeypatch.setattr(proc, "_restart_capture", restart)
    monkeypatch.setattr(proc.capture_supervisor, "wait_while_running", lambda _seconds: True)
    thread = threading.Thread(target=proc._vad_loop)
    thread.start()
    try:
        assert raised.wait(2)
        time.sleep(0.3)
        assert len(restarts) == 1
        assert thread.is_alive()
    finally:
        proc.is_running = False
        thread.join(2)
    assert not thread.is_alive()
