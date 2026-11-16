"""Heartbeat report construction from explicit inputs, without a processor."""

import threading
from types import SimpleNamespace

import pytest

from audio import health
from audio.capture_supervisor import CaptureSupervisor
from audio.config import AudioPipelineConfig
from audio.health import build_health_report
from audio.metrics import PipelineMetrics


@pytest.fixture
def inputs(monkeypatch):
    monkeypatch.setattr(health.time, "monotonic", lambda: 100.0)
    capture = CaptureSupervisor(
        AudioPipelineConfig(), PipelineMetrics(), lambda: None, lambda: True, lambda: None
    )
    capture.last_frame_at = 100.0
    capture.last_loud_at = 100.0
    return {
        "capture": capture,
        "language": "lt",
        "interval_s": 7.0,
        "is_running": True,
        "vad_thread": None,
        "transcription_thread": None,
        "consecutive_transcription_errors": 0,
        "consecutive_utterance_failures": 0,
        "delivery_problem": None,
        "parser_issues": [],
    }


def test_healthy_report_is_empty(inputs):
    assert build_health_report(**inputs) == {
        "language": "lt",
        "interval_s": 7.0,
        "problems": [],
        "issues": [],
    }


@pytest.mark.parametrize(
    "field, value, expected",
    [
        (
            "down",
            True,
            {
                "code": "capture_stopped",
                "params": {},
                "text": "Audio capture stopped; the pipeline is trying to restart it",
            },
        ),
        (
            "last_frame_at",
            93.0,
            {
                "code": "audio_stalled",
                "params": {"seconds": 7},
                "text": "No audio has arrived from the microphone for 7 s",
            },
        ),
        (
            "last_loud_at",
            68.0,
            {
                "code": "mic_silent",
                "params": {"seconds": 32},
                "text": "The microphone is silent: no sound for 32 s. "
                "Check that the right microphone is selected and not muted.",
            },
        ),
    ],
)
def test_capture_issues(inputs, field, value, expected):
    setattr(inputs["capture"], field, value)
    assert build_health_report(**inputs)["issues"] == [expected]


@pytest.mark.parametrize("count", [1, 2])
def test_stt_issue_text_and_count(inputs, count):
    inputs["consecutive_transcription_errors"] = count
    assert build_health_report(**inputs)["issues"] == [
        {
            "code": "stt_failed",
            "params": {"count": count},
            "text": f"Speech recognition failed on the last {count} "
            f"utterance{'s' if count != 1 else ''}",
        }
    ]


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
def test_utterance_failure_threshold(inputs, count):
    inputs["consecutive_utterance_failures"] = count
    expected = (
        []
        if count < 3
        else [
            {
                "code": "utterance_failed",
                "params": {"count": count},
                "text": f"Processing failed on the last {count} utterances; they were skipped",
            }
        ]
    )
    assert build_health_report(**inputs)["issues"] == expected


def test_delivery_issue(inputs):
    inputs["delivery_problem"] = "server unreachable"
    assert build_health_report(**inputs)["issues"] == [
        {
            "code": "delivery_failed",
            "params": {"detail": "server unreachable"},
            "text": "The last detected reference was not delivered: server unreachable",
        }
    ]


def test_issue_order_and_problem_texts(inputs):
    inputs.update(
        vad_thread=threading.Thread(),
        transcription_thread=threading.Thread(),
        consecutive_transcription_errors=2,
        consecutive_utterance_failures=3,
        delivery_problem="server unreachable",
        parser_issues=[
            {"code": "parser_failed", "params": {"language": "lt"}, "text": "Parser failed"},
            {"code": "bible_db_unavailable", "params": {}, "text": "Database unavailable"},
        ],
    )
    inputs["capture"].down = True
    report = build_health_report(**inputs)
    assert [issue["code"] for issue in report["issues"]] == [
        "thread_stopped",
        "thread_stopped",
        "capture_stopped",
        "stt_failed",
        "utterance_failed",
        "delivery_failed",
        "parser_failed",
        "bible_db_unavailable",
    ]
    assert report["issues"][:2] == [
        {
            "code": "thread_stopped",
            "params": {"role": role},
            "text": f"The pipeline's {role} thread has stopped; restart the pipeline",
        }
        for role in ("listening", "transcription")
    ]
    assert report["issues"][-2:] == inputs["parser_issues"]
    assert report["problems"] == [issue["text"] for issue in report["issues"]]


def test_capture_issue_priority(inputs):
    capture = inputs["capture"]
    capture.down = True
    capture.last_frame_at = 90.0
    capture.last_loud_at = 60.0
    assert [issue["code"] for issue in build_health_report(**inputs)["issues"]] == [
        "capture_stopped"
    ]
    capture.down = False
    assert [issue["code"] for issue in build_health_report(**inputs)["issues"]] == ["audio_stalled"]


def test_stopped_pipeline_does_not_report_threads_or_silence(inputs):
    inputs.update(is_running=False, vad_thread=threading.Thread())
    inputs["capture"].last_frame_at = 0.0
    inputs["capture"].last_loud_at = 0.0
    assert build_health_report(**inputs)["issues"] == []


def test_live_thread_is_healthy(inputs):
    inputs["vad_thread"] = SimpleNamespace(is_alive=lambda: True)
    assert build_health_report(**inputs)["issues"] == []
