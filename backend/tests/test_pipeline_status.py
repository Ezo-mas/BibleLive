"""Server side of audio-pipeline health: heartbeats in, status out to the operator."""

import pytest

from core.config import config
from core.server.models import PipelineHeartbeat
from core.server.state import PIPELINE_MISSED_REPORTS_OFFLINE

TOKEN = "pipeline-test-token"


@pytest.fixture
def auth_enabled():
    original = (config.REQUIRE_AUTH, config.OPERATOR_TOKEN)
    config.REQUIRE_AUTH, config.OPERATOR_TOKEN = True, TOKEN
    yield {"Authorization": f"Bearer {TOKEN}"}
    config.REQUIRE_AUTH, config.OPERATOR_TOKEN = original


def _report(problems=(), language="en", interval_s=5.0) -> dict:
    return {"language": language, "interval_s": interval_s, "problems": list(problems)}


def test_status_is_unknown_before_any_report(client):
    response = client.get("/api/pipeline/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "unknown",
        "language": None,
        "problems": [],
        "seconds_since_report": None,
    }


def test_healthy_report_reads_ok(client):
    assert client.post("/api/pipeline/heartbeat", json=_report(language="lt")).status_code == 204

    body = client.get("/api/pipeline/status").json()
    assert body["status"] == "ok"
    assert body["language"] == "lt"
    assert body["problems"] == []
    assert body["seconds_since_report"] is not None


def test_reported_problems_read_degraded(client):
    problems = ["Speech recognition failed on the last 3 utterances"]
    client.post("/api/pipeline/heartbeat", json=_report(problems))

    body = client.get("/api/pipeline/status").json()
    assert body["status"] == "degraded"
    assert body["problems"] == problems


def test_latest_report_replaces_earlier_problems(client):
    client.post("/api/pipeline/heartbeat", json=_report(["Audio capture stopped"]))
    client.post("/api/pipeline/heartbeat", json=_report())

    assert client.get("/api/pipeline/status").json()["status"] == "ok"


def test_silent_pipeline_goes_offline_after_missed_reports(app_state):
    """A pipeline that died cannot say so; missing reports are the signal."""
    report = PipelineHeartbeat(language="en", interval_s=5.0, problems=["Audio capture stopped"])
    app_state.record_pipeline_report(report, now=100.0)
    deadline = 100.0 + 5.0 * PIPELINE_MISSED_REPORTS_OFFLINE

    assert app_state.pipeline_status(now=deadline).status == "degraded"

    offline = app_state.pipeline_status(now=deadline + 0.1)
    assert offline.status == "offline"
    # What a dead pipeline last said is no longer current.
    assert offline.problems == []
    assert offline.seconds_since_report == pytest.approx(15.1)


def test_offline_threshold_follows_the_reported_interval(app_state):
    app_state.record_pipeline_report(
        PipelineHeartbeat(language="en", interval_s=20.0, problems=[]), now=0.0
    )

    assert app_state.pipeline_status(now=59.0).status == "ok"
    assert app_state.pipeline_status(now=61.0).status == "offline"


@pytest.mark.parametrize(
    "report",
    [
        _report(interval_s=0),
        _report(interval_s=301),
        _report(problems=["x"] * 11),
        _report(problems=[""]),
        _report(problems=["x" * 301]),
        _report(language=""),
    ],
)
def test_malformed_reports_are_rejected(client, report):
    assert client.post("/api/pipeline/heartbeat", json=report).status_code == 422
    assert client.get("/api/pipeline/status").json()["status"] == "unknown"


def test_heartbeat_requires_token_when_auth_enabled(client, auth_enabled):
    """Otherwise anyone on the network could tell the operator all is well."""
    assert client.post("/api/pipeline/heartbeat", json=_report()).status_code == 401
    assert (
        client.post("/api/pipeline/heartbeat", json=_report(), headers=auth_enabled).status_code
        == 204
    )


def test_status_requires_token_when_auth_enabled(client, auth_enabled):
    assert client.get("/api/pipeline/status").status_code == 401

    response = client.get("/api/pipeline/status", headers=auth_enabled)
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
