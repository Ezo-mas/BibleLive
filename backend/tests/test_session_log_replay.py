import json
from types import SimpleNamespace

import pytest

from core.config import config
from scripts import replay_session_log

JOHN = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "low"}


def test_replay_posts_to_existing_pipeline_routes(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REQUIRE_AUTH", True)
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "local-test-token")
    monkeypatch.setenv("BIBLELIVE_OPERATOR_TOKEN", "local-test-token")
    records = [
        {"type": "session"},
        {
            "type": "utterance",
            "start_s": 2,
            "end_s": 4,
            "language": "lt",
            "transcripts": [
                {"text": "shaky", "low_confidence_reason": "quiet"},
                {"text": "Jono trečias skyrius"},
            ],
            "references": [{**JOHN, "verse_end": 17, "inferred": True, "from_text": True}],
        },
        {"type": "capture_restart", "at_s": 7},
        {
            "type": "utterance",
            "end_s": 14,
            "language": "en",
            "transcripts": [{"text": "failed", "failed": True}],
        },
        {
            "type": "shutdown_flush",
            "at_s": 24,
            "references": [{"book": "Genesis", "chapter": 1, "confidence": "low"}],
        },
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(map(json.dumps, records)), encoding="utf-8")
    calls = []
    sleeps = []
    now = 0.0

    def sleep(seconds):
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    def post(session, url, *, json, timeout, allow_redirects):
        nonlocal now
        assert url.startswith("http://127.0.0.1:8010")
        assert not session.trust_env
        assert not allow_redirects
        route = url.removeprefix("http://127.0.0.1:8010")
        calls.append((route, json))
        now += 0.25
        return client.post(route, json=json, headers=dict(session.headers))

    monkeypatch.setattr(replay_session_log.requests.Session, "post", post)
    monkeypatch.setattr(
        replay_session_log, "time", SimpleNamespace(monotonic=lambda: now, sleep=sleep)
    )

    assert replay_session_log.main([str(path), "--speed", "10", "--port", "8010"]) == 0
    assert calls == [
        ("/api/transcript", {"text": "Jono trečias skyrius", "language": "lt"}),
        ("/api/reference", {**JOHN, "verse_end": 17, "inferred": True}),
        (
            "/api/reference",
            {
                "book": "Genesis",
                "chapter": 1,
                "verse_start": None,
                "verse_end": None,
                "confidence": "low",
                "inferred": False,
            },
        ),
    ]
    assert sleeps == [1.5]  # Two seconds at 10×, less the first two request times.
    pending = client.get("/api/state", headers={"Authorization": "Bearer local-test-token"}).json()
    assert {reference["book"] for reference in pending["pending"]} == {"John", "Genesis"}


def test_replay_validates_the_file_before_posting(tmp_path, monkeypatch, capsys):
    path = tmp_path / "session.jsonl"
    record = {"type": "utterance", "references": [JOHN]}
    path.write_text(f"{json.dumps(record)}\n{{", encoding="utf-8")

    def unexpected_post(*args, **kwargs):
        pytest.fail("Nothing should be sent from a malformed session log")

    monkeypatch.setattr(replay_session_log.requests.Session, "post", unexpected_post)
    assert replay_session_log.main([str(path)]) == 1
    assert "line 2" in capsys.readouterr().err


@pytest.mark.parametrize("status", [307, 401])
def test_replay_stops_after_a_rejected_post(tmp_path, monkeypatch, capsys, status):
    path = tmp_path / "session.jsonl"
    record = {"type": "utterance", "references": [JOHN, JOHN]}
    path.write_text(json.dumps(record), encoding="utf-8")
    urls = []

    def post(session, url, **kwargs):
        urls.append(url)
        return SimpleNamespace(status_code=status)

    monkeypatch.setattr(replay_session_log.requests.Session, "post", post)
    assert replay_session_log.main([str(path)]) == 1
    assert urls == ["http://127.0.0.1:8000/api/reference"]
    assert f"HTTP {status}" in capsys.readouterr().err
