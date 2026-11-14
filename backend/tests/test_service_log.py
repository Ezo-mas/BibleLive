"""Service events are optional, asynchronous and contain no spoken or verse text."""

import importlib
import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from core.bible_lookup import BibleLookup
from core.bible_versions import BibleVersion
from core.config import Config, config
from core.passage import resolve_passage
from core.server.models import BibleReference, PipelineHeartbeat, PipelineIssue
from core.server.service_log import ServiceLog
from core.server.settings_store import SettingsStore
from core.server.state import AppState


def test_service_log_disabled(monkeypatch):
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", "")
    log = ServiceLog()
    assert log.logger is None
    assert log.listener is None
    assert log.handler is None
    log.emit("shown", text="never logged")
    log.stop()


@pytest.mark.parametrize("directory", [None, "", "service-logs"])
def test_service_log_directory_loaded_from_environment(monkeypatch, directory):
    if directory is None:
        monkeypatch.delenv("BIBLELIVE_SERVICE_LOG_DIR", raising=False)
    else:
        monkeypatch.setenv("BIBLELIVE_SERVICE_LOG_DIR", directory)
    expected = directory or ""
    assert expected == Config(_env_file=None).SERVICE_LOG_DIR


def test_service_log_directory_resolves_against_project_root(monkeypatch, tmp_path):
    config_module = importlib.import_module("core.config")
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    monkeypatch.setattr(config_module, "PROJECT_ROOT", project)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", "")
    assert config.resolve_service_log_dir() is None
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", "logs/service")
    assert config.resolve_service_log_dir() == project / "logs" / "service"
    log = ServiceLog()
    log.emit("cleared")
    log.stop()
    assert len(list((project / "logs" / "service").glob("*.jsonl"))) == 1
    absolute = tmp_path / "absolute"
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", str(absolute))
    assert config.resolve_service_log_dir() == absolute


def test_service_log_allowlist_and_failure_isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", str(tmp_path))
    log = ServiceLog()
    assert log.logger.propagate is False
    log.emit("shown", book="John", text="SECRET", transcript="SECRET", verses=["SECRET"])
    monkeypatch.setattr(log.logger, "info", Mock(side_effect=OSError("disk failed")))
    log.emit("cleared")  # Logging cannot fail an operator request.
    log.stop()
    raw = next(tmp_path.glob("*.jsonl")).read_text()
    assert "SECRET" not in raw
    event = json.loads(raw)
    assert set(event) == {"ts", "event", "book"}
    assert datetime.fromisoformat(event["ts"]).utcoffset().total_seconds() == 0


def test_service_log_unusable_directory(monkeypatch, tmp_path, caplog):
    path = tmp_path / "file"
    path.write_text("not a directory")
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", str(path))
    log = ServiceLog()
    assert log.logger is None
    assert str(path) in caplog.text
    assert "Could not start service log" in caplog.text
    log.emit("cleared")
    log.stop()


@pytest.mark.asyncio
async def test_server_events_and_pipeline_status_changes(monkeypatch, tmp_path, bible_db, kjv_db):
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(config, "MAX_PENDING_REFERENCES", 1)
    state = AppState(BibleLookup(str(bible_db)), SettingsStore(tmp_path / "settings.json"))
    state.auto_mode = "high"
    await state.add_reference(
        BibleReference(
            book="John", chapter=3, verse_start=16, confidence="high", inferred=True, text="SECRET"
        )
    )
    state.auto_mode = "off"
    await state.add_reference(BibleReference(book="John", chapter=3, verse_start=16))
    await state.add_reference(BibleReference(book="John", chapter=3, verse_start=17))
    await state.clear_pending_and_notify()
    await state.clear_pending_and_notify()
    await state.display_manual_passage(resolve_passage(state.bible_lookup, "John", 3, 17))
    await state.clear_display()
    monkeypatch.setattr(
        state, "available_bible_versions", lambda: {"kjv": BibleVersion(id="kjv", path=kjv_db)}
    )
    state.switch_bible_version("kjv")
    healthy = PipelineHeartbeat(language="en", interval_s=5)
    state.record_pipeline_report(healthy, now=100)
    state.record_pipeline_report(healthy, now=101)
    state.pipeline_status(now=102)
    sick = PipelineHeartbeat(
        language="en",
        interval_s=5,
        problems=["SECRET"],
        issues=[PipelineIssue(code="mic_silent", text="SECRET")],
    )
    state.record_pipeline_report(sick, now=102)
    changed = PipelineHeartbeat(
        language="en",
        interval_s=5,
        problems=["SECRET"],
        issues=[
            PipelineIssue(code="capture_stalled", text="SECRET"),
            PipelineIssue(code="mic_silent", text="SECRET"),
        ],
    )
    state.record_pipeline_report(changed, now=103)
    changed.issues.reverse()
    state.record_pipeline_report(changed, now=104)
    state.pipeline_status(now=200)
    await state.shutdown()
    raw = next((tmp_path / "logs").glob("*.jsonl")).read_text()
    assert "SECRET" not in raw
    assert "For God" not in raw
    assert '"text"' not in raw
    events = [json.loads(line) for line in raw.splitlines()]
    assert {e["event"] for e in events} == {
        "shown",
        "cleared",
        "queue_cleared",
        "queue_overflowed",
        "translation_switched",
        "pipeline_status",
    }
    shown = [e for e in events if e["event"] == "shown"]
    assert shown[0]["source"] == "detected"
    assert shown[0]["confidence"] == "high"
    assert shown[0]["inferred"] is True
    assert shown[0]["auto_approved"] is True
    assert shown[1]["source"] == "manual"
    assert shown[1]["auto_approved"] is False
    statuses = [e for e in events if e["event"] == "pipeline_status"]
    assert [e["status"] for e in statuses] == ["ok", "degraded", "degraded", "offline"]
    assert statuses[1]["issue_codes"] == ["mic_silent"]
    assert set(statuses[2]["issue_codes"]) == {"mic_silent", "capture_stalled"}
    assert len([e for e in events if e["event"] == "queue_cleared"]) == 1
    switch = next(e for e in events if e["event"] == "translation_switched")
    assert (switch["from"], switch["to"]) == ("sample", "kjv")


@pytest.mark.asyncio
async def test_no_text_auto_approval_is_silent_and_both_refusals_are_logged(
    monkeypatch, tmp_path, bible_db, caplog
):
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", str(tmp_path / "logs"))
    state = AppState(BibleLookup(str(bible_db)), SettingsStore(tmp_path / "settings.json"))
    state.connection_manager.send_to_operator = AsyncMock()
    state.connection_manager.send_to_display = AsyncMock()
    monkeypatch.setattr(state.bible_lookup, "get_verse_range", lambda *_: [])
    state.auto_mode = "high"
    auto = BibleReference(book="John", chapter=3, verse_start=16, confidence="high")
    await state.add_reference(auto)
    messages = [c.args[0] for c in state.connection_manager.send_to_operator.await_args_list]
    assert [m["type"] for m in messages] == ["pending_update", "pending_update"]
    assert messages[-1]["data"]["count"] == 0
    assert state.current_display is None
    assert not state.reference_history
    state.connection_manager.send_to_display.assert_not_awaited()
    state.auto_mode = "off"
    manual = BibleReference(book="John", chapter=3, verse_start=17, confidence="high")
    await state.add_reference(manual)
    await state.approve_and_notify(manual.id)
    await state.shutdown()
    events = [
        json.loads(line)
        for line in next((tmp_path / "logs").glob("*.jsonl")).read_text().splitlines()
    ]
    assert [e["event"] for e in events] == ["refused_no_text", "refused_no_text"]
    assert [e["auto_approved"] for e in events] == [True, False]
    assert [e["verse_start"] for e in events] == [16, 17]
    assert "Refused approval without text" in caplog.text
