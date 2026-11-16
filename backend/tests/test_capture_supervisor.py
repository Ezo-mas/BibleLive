"""Capture recovery and health tracking without the pipeline or worker threads."""

import logging
from array import array
from types import SimpleNamespace

import pytest

from audio import capture_supervisor as supervisor_module
from audio.capture_supervisor import SILENT_MIC_S, CaptureSupervisor
from audio.config import AudioPipelineConfig
from audio.metrics import PipelineMetrics


@pytest.fixture
def setup_supervisor(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(supervisor_module.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(supervisor_module.time, "sleep", lambda seconds: None)
    capture = SimpleNamespace(failed=False, captured_seconds=20.0)
    running = SimpleNamespace(value=True)
    calls = []
    capture.stop_capture = lambda: calls.append("stop")
    capture.start_capture = lambda device: calls.append(("start", device))
    session_log = SimpleNamespace(event=lambda name: calls.append(name))
    supervisor = CaptureSupervisor(
        AudioPipelineConfig(
            capture_restart_base_delay=1.0,
            capture_restart_max_delay=4.0,
            capture_healthy_s=10.0,
            capture_stall_restart_s=5.0,
            capture_first_frame_grace_s=15.0,
        ),
        PipelineMetrics(),
        lambda: capture,
        lambda: running.value,
        lambda: session_log,
    )
    return supervisor, capture, running, clock, calls


def test_restart_doubles_and_caps_delays_and_preserves_side_effect_order(
    setup_supervisor, monkeypatch, caplog
):
    supervisor, capture, _, _, calls = setup_supervisor
    attempts = 0

    def start(device):
        nonlocal attempts
        calls.append(("start", device))
        attempts += 1
        if attempts < 4:
            raise RuntimeError("no audio device")

    def flush():
        assert supervisor.down
        assert supervisor.metrics.capture_failures == 1
        calls.append("flush/discard/queue")

    capture.start_capture = start
    monkeypatch.setattr(
        supervisor, "wait_while_running", lambda seconds: calls.append(("wait", seconds)) or True
    )
    with caplog.at_level(logging.INFO, logger="audio.pipeline"):
        supervisor.restart("audio stalled", flush)

    assert calls == [
        "flush/discard/queue",
        ("wait", 1.0),
        "stop",
        ("start", None),
        ("wait", 2.0),
        "stop",
        ("start", None),
        ("wait", 4.0),
        "stop",
        ("start", None),
        ("wait", 4.0),
        "stop",
        ("start", None),
        "capture_restarted",
    ]
    assert supervisor.metrics.capture_restarts == 1
    assert supervisor.down  # Relaunch alone must not report healthy capture.
    assert supervisor.restarted_at_s == 20.0
    assert caplog.messages[0] == (
        "Audio capture failed (audio stalled); no references can be detected "
        "until it restarts. Restarting in 1s (attempt 1)"
    )
    assert caplog.messages.count("Audio capture restart failed: no audio device") == 3
    assert caplog.messages[-1] == "Audio capture restarted; listening for Bible references again"


def test_backoff_carries_over_until_stream_is_healthy(setup_supervisor, monkeypatch):
    supervisor, capture, _, clock, _ = setup_supervisor
    waits = []
    monkeypatch.setattr(
        supervisor, "wait_while_running", lambda seconds: waits.append(seconds) or True
    )
    supervisor.restart("ffmpeg exited", lambda: None)
    capture.captured_seconds = 29.9
    supervisor.note_frame(b"\0" * 960)
    assert supervisor.restart_attempt == 1
    assert not supervisor.down
    assert supervisor.last_frame_at == clock.now

    supervisor.restart("ffmpeg exited", lambda: None)
    capture.captured_seconds = 39.8
    supervisor.note_capture_healthy()
    assert supervisor.restart_attempt == 2
    capture.captured_seconds = 39.9
    supervisor.note_capture_healthy()
    assert supervisor.restart_attempt == 0
    assert supervisor.restarted_at_s is None
    supervisor.restart("ffmpeg exited", lambda: None)
    assert waits == [1.0, 2.0, 1.0]


@pytest.mark.parametrize("failed, cause", [(True, "ffmpeg exited"), (False, "audio stalled")])
def test_watchdog_exit_and_stall_causes(setup_supervisor, failed, cause):
    supervisor, capture, _, clock, _ = setup_supervisor
    supervisor.begin_watchdog()
    supervisor.note_frame(b"\0" * 960)
    capture.failed = failed
    clock.now += 5.0 if not failed else 0.0
    restarts = []
    supervisor.note_missing_frame(restarts.append)
    assert restarts == [cause]


def test_first_frame_grace_then_normal_stall_and_fresh_grace(setup_supervisor):
    supervisor, _, _, clock, _ = setup_supervisor
    supervisor.begin_watchdog()
    restarts = []
    clock.now += 14.9
    supervisor.note_missing_frame(restarts.append)
    assert restarts == []
    clock.now = 115.0
    supervisor.note_missing_frame(restarts.append)
    assert restarts == ["audio stalled"]
    supervisor.note_frame(b"\0" * 960)
    clock.now += 5.0
    supervisor.note_missing_frame(restarts.append)
    assert restarts == ["audio stalled", "audio stalled"]
    clock.now += 5.0
    supervisor.note_missing_frame(restarts.append)
    assert len(restarts) == 2


def test_watchdog_resets_grace_even_when_restart_raises(setup_supervisor):
    supervisor, _, _, clock, _ = setup_supervisor
    supervisor.begin_watchdog()
    supervisor.note_frame(b"\0" * 960)
    clock.now += 5.0

    def fail(_cause):
        raise RuntimeError("flush failed")

    with pytest.raises(RuntimeError, match="flush failed"):
        supervisor.note_missing_frame(fail)
    clock.now += 5.0
    restarts = []
    supervisor.note_missing_frame(restarts.append)
    assert restarts == []


def test_stop_during_wait_wakes_early_without_starting_capture(setup_supervisor, monkeypatch):
    supervisor, _, running, clock, calls = setup_supervisor
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds
        running.value = False

    monkeypatch.setattr(supervisor_module.time, "sleep", sleep)
    supervisor.restart("ffmpeg exited", lambda: calls.append("flush"))
    assert calls == ["flush"]
    assert sleeps == [0.1]
    assert supervisor.metrics.capture_restarts == 0


def test_stop_after_wait_is_rechecked_under_capture_lock(setup_supervisor, monkeypatch):
    supervisor, _, running, _, calls = setup_supervisor

    def wait(_seconds):
        running.value = False
        return True

    monkeypatch.setattr(supervisor, "wait_while_running", wait)
    supervisor.restart("ffmpeg exited", lambda: calls.append("flush"))
    assert calls == ["flush"]


def test_silent_microphone_and_recovery_log_only_transitions(setup_supervisor, caplog):
    supervisor, _, _, clock, _ = setup_supervisor
    supervisor.initialize_health()
    with caplog.at_level(logging.INFO, logger="audio.pipeline"):
        supervisor.note_microphone_level(b"\0" * 960, clock.now + SILENT_MIC_S)
        assert caplog.messages == []
        for seconds in (31.0, 32.0, 33.0):
            supervisor.note_microphone_level(b"\0" * 960, clock.now + seconds)
        assert supervisor.mic_silent_logged
        for seconds in (34.0, 35.0):
            supervisor.note_microphone_level(array("h", [100] * 480).tobytes(), clock.now + seconds)

    assert caplog.messages == [
        "The microphone is silent: no sound above -70 dBFS for 30 s. Check that the "
        "right microphone is selected (--input-device) and not muted.",
        "The microphone is picking up sound again",
    ]
    assert not supervisor.mic_silent_logged
    assert supervisor.last_loud_at == 135.0
