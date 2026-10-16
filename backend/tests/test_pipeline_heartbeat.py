"""Pipeline side of health reporting: what it reports, and how it sends it."""

import logging
import threading
import time
from types import SimpleNamespace

import pytest
import requests

from audio.config import AudioPipelineConfig
from audio.heartbeat import HeartbeatSender
from audio.metrics import PipelineMetrics
from audio.pipeline import AUDIO_STALL_S, BibleLiveAudioProcessor
from audio.stt import Transcription

# Requires the optional audio extras (faster-whisper, webrtcvad, numpy).
pytestmark = pytest.mark.audio


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeSTT:
    """Returns the queued transcriptions in order."""

    def __init__(self, *results: Transcription):
        self.language = "en"
        self.results = list(results)

    def transcribe_utterance(self, pcm, language=None):  # noqa: ARG002 - signature parity
        return self.results.pop(0)


def _processor(stt=None, **config) -> BibleLiveAudioProcessor:
    """A processor with just enough wiring to report its health."""
    proc = BibleLiveAudioProcessor.__new__(BibleLiveAudioProcessor)
    proc.config = AudioPipelineConfig(**config)
    proc.is_running = True
    proc.stt = stt or _FakeSTT()
    proc.metrics = PipelineMetrics()
    proc._frame_duration_s = 0.03
    proc._local = threading.local()
    proc.vad_thread = None
    proc.transcription_thread = None
    proc.sender = SimpleNamespace(delivery_problem=None)
    proc.transcript_sender = SimpleNamespace(submit=lambda text, language: None)
    proc._capture_down = False
    proc._last_frame_at = time.monotonic()
    proc._consecutive_transcription_errors = 0
    proc._parser_problems = []
    proc.verse_text_db_path = None  # set by __init__ in the real pipeline
    return proc


# ---------------------------------------------------------------------------
# health_report
# ---------------------------------------------------------------------------
def test_healthy_pipeline_reports_no_problems():
    report = _processor(heartbeat_interval=7.0).health_report()

    assert report == {"language": "en", "interval_s": 7.0, "problems": []}


def test_capture_outage_is_reported():
    proc = _processor()
    proc._capture_down = True

    assert proc.health_report()["problems"] == [
        "Audio capture stopped; the pipeline is trying to restart it"
    ]


def test_stalled_capture_is_reported():
    """ffmpeg can hang without exiting; then frames just stop arriving."""
    proc = _processor()
    proc._last_frame_at = time.monotonic() - (AUDIO_STALL_S + 2)

    [problem] = proc.health_report()["problems"]
    assert problem.startswith("No audio has arrived from the microphone for")


def test_a_restart_in_progress_is_not_also_reported_as_a_stall():
    proc = _processor()
    proc._capture_down = True
    proc._last_frame_at = time.monotonic() - (AUDIO_STALL_S + 2)

    assert proc.health_report()["problems"] == [
        "Audio capture stopped; the pipeline is trying to restart it"
    ]


def test_frames_arriving_clear_a_stall():
    proc = _processor()
    proc._last_frame_at = time.monotonic() - (AUDIO_STALL_S + 2)
    capture = SimpleNamespace(
        failed=False,
        captured_seconds=0.0,
        audio_queue=SimpleNamespace(qsize=lambda: 0),
        get_audio_frame=lambda ms: b"\0" * 960,  # noqa: ARG005 - signature parity
    )
    proc.audio_capture = capture
    proc._capture_restarted_at_s = None
    proc._pending_language = None
    proc._pending_language_lock = threading.Lock()
    proc._capture_language = "en"
    proc.frame_duration_ms = 30
    proc.vad_queue = SimpleNamespace(qsize=lambda: 0)
    proc.last_metrics_log = time.time()

    # One pass of the VAD loop: the segmenter stops it after the first frame.
    def push_once(frame):  # noqa: ARG001 - signature parity
        proc.is_running = False

    proc.segmenter = SimpleNamespace(push=push_once)
    proc._vad_loop()
    proc.is_running = True

    assert proc.health_report()["problems"] == []


def test_transcription_errors_are_counted_until_one_succeeds():
    proc = _processor(
        _FakeSTT(
            Transcription(None, 1.0, failed=True),
            Transcription(None, 1.0, failed=True),
            Transcription(None, 0.0),  # silence: no text, but not a failure
        )
    )

    proc._process_utterance("en", [b"\0"])
    proc._process_utterance("en", [b"\0"])
    assert proc.health_report()["problems"] == [
        "Speech recognition failed on the last 2 utterances"
    ]

    proc._process_utterance("en", [b"\0"])
    assert proc.health_report()["problems"] == []


def test_undelivered_reference_is_reported():
    proc = _processor()
    proc.sender.delivery_problem = "the server kept failing or did not answer"

    assert proc.health_report()["problems"] == [
        "The last detected reference was not delivered: the server kept failing or did not answer"
    ]


def test_dead_worker_thread_is_reported():
    proc = _processor()
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    proc.transcription_thread = dead

    assert proc.health_report()["problems"] == [
        "The pipeline's transcription thread has stopped; restart the pipeline"
    ]


def test_missing_bible_database_is_reported(tmp_path):
    proc = _processor()
    proc.bible_db_path = str(tmp_path / "missing.sqlite")

    proc._init_thread_local_parsers()

    problems = proc.health_report()["problems"]
    assert len(problems) == 1
    assert "could not open the Bible database" in problems[0]
    # Parsing itself still works without the validator.
    assert set(proc._local.streaming_parsers) == {"en", "lt"}


# ---------------------------------------------------------------------------
# HeartbeatSender
# ---------------------------------------------------------------------------
def _sender(get_status=lambda: {"language": "en", "interval_s": 5.0, "problems": []}, **config):
    return HeartbeatSender("http://server", AudioPipelineConfig(**config), get_status)


def test_beat_posts_the_status_with_the_token(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "audio.heartbeat.requests.post",
        lambda url, **kwargs: calls.append((url, kwargs)) or _Response(204),
    )

    assert _sender(api_token="secret", heartbeat_interval=2.0).beat() is True

    [(url, kwargs)] = calls
    assert url == "http://server/api/pipeline/heartbeat"
    assert kwargs["json"] == {"language": "en", "interval_s": 5.0, "problems": []}
    assert kwargs["headers"] == {"Authorization": "Bearer secret"}
    # Never wait longer than the gap to the next heartbeat.
    assert kwargs["timeout"] == 2.0


def test_beat_sends_no_auth_header_without_a_token(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "audio.heartbeat.requests.post",
        lambda url, **kwargs: calls.append(kwargs) or _Response(204),
    )

    _sender().beat()

    assert calls[0]["headers"] == {}


def test_unauthorized_beat_is_logged_once_as_an_error(monkeypatch, caplog):
    monkeypatch.setattr("audio.heartbeat.requests.post", lambda url, **kwargs: _Response(401))
    sender = _sender()

    with caplog.at_level(logging.WARNING, logger="audio.heartbeat"):
        assert sender.beat() is False
        assert sender.beat() is False

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "BIBLELIVE_OPERATOR_TOKEN" in errors[0].getMessage()


def test_unreachable_server_is_logged_once_then_recovery_is_logged(monkeypatch, caplog):
    outcomes = iter(
        [requests.exceptions.ConnectionError("refused")] * 2 + [_Response(204)],
    )

    def fake_post(url, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("audio.heartbeat.requests.post", fake_post)
    sender = _sender()

    with caplog.at_level(logging.INFO, logger="audio.heartbeat"):
        assert [sender.beat(), sender.beat(), sender.beat()] == [False, False, True]

    messages = [r.getMessage() for r in caplog.records]
    assert sum("Could not reach the server" in m for m in messages) == 1
    assert messages[-1] == "Health reports are reaching the server again"


def test_thread_reports_immediately_and_stops_promptly(monkeypatch):
    beats = threading.Event()
    monkeypatch.setattr(
        "audio.heartbeat.requests.post", lambda url, **kwargs: beats.set() or _Response(204)
    )
    sender = _sender(heartbeat_interval=60.0)

    sender.start()
    try:
        assert beats.wait(2.0), "the first report should not wait a whole interval"
    finally:
        started = time.monotonic()
        sender.stop()
    assert time.monotonic() - started < 1.0
    assert sender._thread is None
