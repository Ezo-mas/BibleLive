"""Pipeline side of health reporting: what it reports, and how it sends it."""

import logging
import threading
import time
from array import array
from types import SimpleNamespace

import pytest
import requests

from audio.config import AudioPipelineConfig
from audio.heartbeat import HeartbeatSender
from audio.metrics import PipelineMetrics
from audio.pipeline import AUDIO_STALL_S, SILENT_MIC_S, BibleLiveAudioProcessor, frame_is_loud
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
    proc._last_loud_at = time.monotonic()
    proc._mic_silent_logged = False
    proc._consecutive_transcription_errors = 0
    proc._parser_issues = []
    proc.verse_text_db_path = None  # set by __init__ in the real pipeline
    return proc


# ---------------------------------------------------------------------------
# health_report
# ---------------------------------------------------------------------------
def test_healthy_pipeline_reports_no_problems():
    report = _processor(heartbeat_interval=7.0).health_report()

    assert report == {"language": "en", "interval_s": 7.0, "problems": [], "issues": []}


def test_capture_outage_is_reported():
    proc = _processor()
    proc._capture_down = True

    assert proc.health_report()["problems"] == [
        "Audio capture stopped; the pipeline is trying to restart it"
    ]


def test_capture_outage_issue_has_its_code():
    proc = _processor()
    proc._capture_down = True

    assert proc.health_report()["issues"] == [
        {
            "code": "capture_stopped",
            "params": {},
            "text": "Audio capture stopped; the pipeline is trying to restart it",
        }
    ]


def test_stalled_capture_is_reported():
    """ffmpeg can hang without exiting; then frames just stop arriving."""
    proc = _processor()
    proc._last_frame_at = time.monotonic() - (AUDIO_STALL_S + 2)

    [problem] = proc.health_report()["problems"]
    assert problem.startswith("No audio has arrived from the microphone for")


def test_stalled_capture_issue_gives_whole_seconds():
    proc = _processor()
    proc._last_frame_at = time.monotonic() - (AUDIO_STALL_S + 2)

    [issue] = proc.health_report()["issues"]
    seconds = issue["params"]["seconds"]
    assert issue["code"] == "audio_stalled"
    assert isinstance(seconds, int) and seconds >= AUDIO_STALL_S
    assert issue["text"] == f"No audio has arrived from the microphone for {seconds} s"


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


@pytest.mark.parametrize(
    ("errors", "text"),
    [
        (1, "Speech recognition failed on the last 1 utterance"),
        (2, "Speech recognition failed on the last 2 utterances"),
    ],
)
def test_transcription_failure_issue_counts_the_utterances(errors, text):
    proc = _processor()
    proc._consecutive_transcription_errors = errors

    assert proc.health_report()["issues"] == [
        {"code": "stt_failed", "params": {"count": errors}, "text": text}
    ]


def test_undelivered_reference_is_reported():
    proc = _processor()
    proc.sender.delivery_problem = "the server kept failing or did not answer"

    assert proc.health_report()["problems"] == [
        "The last detected reference was not delivered: the server kept failing or did not answer"
    ]
    assert proc.health_report()["issues"] == [
        {
            "code": "delivery_failed",
            "params": {"detail": "the server kept failing or did not answer"},
            "text": "The last detected reference was not delivered: "
            "the server kept failing or did not answer",
        }
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
    assert proc.health_report()["issues"] == [
        {
            "code": "thread_stopped",
            "params": {"role": "transcription"},
            "text": "The pipeline's transcription thread has stopped; restart the pipeline",
        }
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
    assert proc.health_report()["issues"] == [
        {
            "code": "bible_db_unavailable",
            "params": {},
            "text": problems[0],
        }
    ]


def test_failed_parser_issue_names_its_language(tmp_path, monkeypatch):
    proc = _processor()
    proc.bible_db_path = str(tmp_path / "missing.sqlite")
    real_make_parser = BibleLiveAudioProcessor._make_parser

    def make_parser(language, validator, verse_text, source):
        if language == "lt":
            raise RuntimeError("model missing")
        return real_make_parser(language, validator, verse_text, source)

    monkeypatch.setattr(proc, "_make_parser", make_parser)
    proc._init_thread_local_parsers()

    assert [i for i in proc.health_report()["issues"] if i["code"] == "parser_failed"] == [
        {
            "code": "parser_failed",
            "params": {"language": "lt"},
            "text": "The 'lt' reference parser failed to start, so no references are "
            "detected in that language",
        }
    ]
    assert set(proc._local.streaming_parsers) == {"en"}


# ---------------------------------------------------------------------------
# Microphone level: a dead input still delivers frames, so only the level shows it
# ---------------------------------------------------------------------------
def _tone(dbfs: float) -> bytes:
    """A 30 ms frame at a constant level, whose RMS is ``dbfs`` (16-bit full scale)."""
    return array("h", [round(32768 * 10 ** (dbfs / 20))] * 480).tobytes()


def test_room_noise_and_speech_are_sound():
    assert frame_is_loud(_tone(-60.0))  # a quiet room
    assert frame_is_loud(_tone(-45.0))  # a normal room
    assert frame_is_loud(_tone(-20.0))  # speech


def test_dead_input_and_digital_silence_are_not_sound():
    assert not frame_is_loud(_tone(-80.0))  # the 2026-10 incident
    assert not frame_is_loud(b"\0" * 960)


def test_sound_threshold_is_minus_70_dbfs():
    assert not frame_is_loud(_tone(-70.3))  # amplitude 10
    assert frame_is_loud(_tone(-69.4))  # amplitude 11


def test_sound_restarts_the_silence_clock_and_silence_does_not():
    proc = _processor()
    proc._last_loud_at = 100.0

    proc._note_microphone_level(_tone(-80.0), 110.0)
    assert proc._last_loud_at == 100.0

    proc._note_microphone_level(_tone(-60.0), 111.0)
    assert proc._last_loud_at == 111.0


def test_dead_microphone_is_reported_after_30_seconds_and_clears_with_sound(caplog):
    proc = _processor()
    start = time.monotonic() - 40  # 40 s of silent frames, ending now
    proc._last_loud_at = start

    with caplog.at_level(logging.INFO, logger="audio.pipeline"):
        for frame_no in range(int(40 / 0.03)):
            proc._note_microphone_level(_tone(-80.0), start + frame_no * 0.03)

        [problem] = proc.health_report()["problems"]
        assert problem.startswith("The microphone is silent: no sound for ")
        assert "right microphone" in problem

        # Logged once when the silence passes the limit, not on every frame.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

        proc._note_microphone_level(_tone(-45.0), time.monotonic())

        assert proc.health_report()["problems"] == []
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert infos == ["The microphone is picking up sound again"]


def test_a_quiet_room_between_speech_is_not_reported(caplog):
    """Room noise at -60 dBFS is sound, so a minute of it never trips the warning."""
    proc = _processor()
    start = time.monotonic() - 60
    proc._last_loud_at = start
    room = _tone(-60.0)

    with caplog.at_level(logging.WARNING, logger="audio.pipeline"):
        for frame_no in range(int(60 / 0.03)):
            proc._note_microphone_level(room, start + frame_no * 0.03)

    assert proc.health_report()["problems"] == []
    assert not caplog.records


def test_a_stalled_capture_is_not_also_reported_as_a_silent_microphone():
    proc = _processor()
    proc._last_loud_at = time.monotonic() - (SILENT_MIC_S + 2)
    proc._last_frame_at = time.monotonic() - (AUDIO_STALL_S + 2)

    [problem] = proc.health_report()["problems"]
    assert problem.startswith("No audio has arrived from the microphone for")


def test_silent_microphone_issue_gives_whole_seconds():
    proc = _processor()
    proc._last_loud_at = time.monotonic() - (SILENT_MIC_S + 2)

    [issue] = proc.health_report()["issues"]
    seconds = issue["params"]["seconds"]
    assert issue["code"] == "mic_silent"
    assert isinstance(seconds, int) and seconds >= SILENT_MIC_S
    assert issue["text"] == (
        f"The microphone is silent: no sound for {seconds} s. "
        "Check that the right microphone is selected and not muted."
    )


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


def test_beat_sends_the_issues_of_the_health_report(monkeypatch):
    proc = _processor()
    proc._capture_down = True
    calls = []
    monkeypatch.setattr(
        "audio.heartbeat.requests.post",
        lambda url, **kwargs: calls.append(kwargs) or _Response(204),
    )

    assert _sender(get_status=proc.health_report).beat() is True

    body = calls[0]["json"]
    assert [issue["code"] for issue in body["issues"]] == ["capture_stopped"]
    assert body["problems"] == [issue["text"] for issue in body["issues"]]


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
