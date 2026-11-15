"""Settings persistence and coordinate-only screen recovery."""

import asyncio
import json
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from core.bible_lookup import BibleLookup
from core.bible_versions import BibleVersion
from core.config import config
from core.server import app as server_app
from core.server import state as server_state
from core.server.models import BibleReference
from core.server.runtime_state import RESTORE_DISPLAY_MAX_AGE_S, RuntimeReference, RuntimeStateStore
from core.server.settings_store import SettingsStore
from core.server.state import AppState


def reference(**changes):
    return BibleReference(book="John", chapter=3, verse_start=16, source="manual", **changes)


def recovery_file(tmp_path, *, age=0, verse=16, translation="sample"):
    store = RuntimeStateStore(tmp_path / "runtime_state.json")
    record = RuntimeReference.from_reference(reference(), translation)
    record.shown_at = time.time() - age
    record.verse_start = verse
    store._writer.write_json({"current": record.model_dump(), "history": [record.model_dump()]})
    return store.path


def startup(monkeypatch, tmp_path, bible_db, *, injected=False):
    monkeypatch.setattr(config, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(config, "AUTO_BIBLE_VERSION", False)
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", "")
    lookup = BibleLookup(str(bible_db))
    monkeypatch.setattr(server_app, "create_bible_lookup", lambda: lookup)
    return server_app.create_app(lookup if injected else None)


@pytest.mark.parametrize(
    "age,restored",
    [(0, True), (599, True), (RESTORE_DISPLAY_MAX_AGE_S, False), (601, False), (-60, False)],
)
def test_restore_age_and_display_snapshot(monkeypatch, tmp_path, bible_db, age, restored):
    recovery_file(tmp_path, age=age)
    application = startup(monkeypatch, tmp_path, bible_db)
    state = application.state.app_state
    assert bool(state.current_display) is restored
    assert not state.pending_references
    assert len(state.reference_history) == int(restored)
    if not restored:
        saved = json.loads((tmp_path / "runtime_state.json").read_text())
        assert saved["current"] is None
        assert saved["history"] == []
    if restored:
        saved = json.loads((tmp_path / "runtime_state.json").read_text())
        assert state.current_display is state.reference_history[0]
        assert datetime.fromisoformat(state.current_display.created_at) == datetime.fromtimestamp(
            saved["current"]["shown_at"], UTC
        )
    with TestClient(application) as client, client.websocket_connect("/ws/display") as display:
        message = display.receive_json()
        assert message["type"] == ("reference" if restored else "clear")
        if restored:
            assert message["data"]["source"] == "manual"
            assert "For God so loved" in message["data"]["text"]
        assert display.receive_json()["type"] == "display_settings"


def test_restore_missing_verse_warns(monkeypatch, tmp_path, bible_db, caplog):
    recovery_file(tmp_path, verse=18)  # This translation ends John 3 at verse 17.
    application = startup(monkeypatch, tmp_path, bible_db)
    with TestClient(application):
        state = application.state.app_state
        assert state.current_display is None
        assert state.reference_history == []
    saved = json.loads((tmp_path / "runtime_state.json").read_text())
    assert saved["current"] is None
    assert saved["history"] == []
    assert "no longer resolves" in caplog.text


@pytest.mark.parametrize("content", [None, "{broken", "[]", '{"current": {"book": "John"}}'])
def test_missing_or_damaged_runtime_file(monkeypatch, tmp_path, bible_db, caplog, content):
    if content is not None:
        (tmp_path / "runtime_state.json").write_text(content)
    application = startup(monkeypatch, tmp_path, bible_db)
    with TestClient(application):
        assert application.state.app_state.current_display is None
    assert ("Ignoring runtime state" in caplog.text) is (content is not None)


def test_injected_lookup_does_not_restore(monkeypatch, tmp_path, bible_db):
    recovery_file(tmp_path)
    with TestClient(startup(monkeypatch, tmp_path, bible_db, injected=True)) as client:
        assert client.get("/api/state").json()["current_display"] is None


@pytest.mark.asyncio
async def test_runtime_coalesces_latest_and_never_stores_text(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime_state.json")
    write = Mock(wraps=store._writer.write_json)
    store._writer.write_json = write
    for verse in range(1, 51):
        ref = BibleReference(book="John", chapter=3, verse_start=verse, text="SECRET")
        store.record(ref, "kjv")
    assert not store.path.exists()  # Nothing has awaited a disk write.
    await store.flush()
    assert write.call_count == 1
    saved = json.loads(store.path.read_text())
    assert saved["current"]["verse_start"] == 50
    assert len(saved["history"]) == 20
    assert "SECRET" not in store.path.read_text()
    assert "text" not in store.path.read_text()
    store.clear()
    await store.flush()
    assert json.loads(store.path.read_text())["current"] is None
    assert len(json.loads(store.path.read_text())["history"]) == 20


@pytest.mark.asyncio
async def test_settings_round_trip_and_shutdown_flush(tmp_path, bible_db, monkeypatch):
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", "")
    settings = SettingsStore(tmp_path / "settings.json")
    state = AppState(BibleLookup(str(bible_db)), settings)
    assert await state.set_verse_limit("all")
    assert await state.set_display_theme("light")
    assert await state.set_clear_mode("blank")
    ref = reference()
    state._resolve_verses(ref)
    state.current_display = ref
    state._record_history(ref)
    await state.shutdown()
    restored = AppState(BibleLookup(str(bible_db)), settings)
    assert restored.display_settings.verse_limit == "all"
    assert restored.display_settings.theme == "light"
    assert restored.display_settings.clear_mode == "blank"
    restored.restore_runtime_state()
    assert restored.current_display.verse_start == 16
    assert "text" not in restored.runtime_state.path.read_text()
    await restored.clear_display()
    await restored.shutdown()
    assert json.loads((tmp_path / "runtime_state.json").read_text())["current"] is None


@pytest.mark.parametrize("limit", [None, 0, 4, "5", True, [], {}])
@pytest.mark.parametrize("theme", [None, "blue", 3, [], {}])
def test_invalid_saved_display_settings(tmp_path, limit, theme):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"verse_limit": limit, "theme": theme}))
    store = SettingsStore(path)
    assert store.load_verse_limit() == 3
    assert store.load_theme() == "dark"


@pytest.mark.parametrize("limit", [3, 5, 10, "all"])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_valid_saved_display_settings(tmp_path, limit, theme):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.save_verse_limit(limit)
    assert store.save_theme(theme)
    assert store.load_verse_limit() == limit
    assert store.load_theme() == theme


@pytest.mark.asyncio
async def test_runtime_changes_during_write_are_flushed(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime_state.json")
    started, release = threading.Event(), threading.Event()
    original_write = store._writer.write_json

    def slow_write(data):
        started.set()
        assert release.wait(5), "test did not release the disk writer"
        return original_write(data)

    store._writer.write_json = slow_write
    store.record(reference(), "sample")
    try:
        assert await asyncio.to_thread(started.wait, 2)
        # The server can keep changing state while the disk thread is stalled.
        for verse in range(1, 51):
            store.record(BibleReference(book="John", chapter=3, verse_start=verse), "sample")
        store.clear()
    finally:
        release.set()
        await store.flush()
    saved = json.loads(store.path.read_text())
    assert saved["current"] is None
    assert saved["history"][0]["verse_start"] == 50


def test_restore_runs_after_saved_translation(monkeypatch, tmp_path, bible_db, kjv_db):
    recovery_file(tmp_path, verse=18, translation="kjv")
    monkeypatch.setattr(config, "DB_PATH", str(bible_db))
    SettingsStore(tmp_path / "settings.json").save_bible_version(
        "kjv", str(config.resolve_db_path())
    )
    monkeypatch.setattr(
        AppState, "available_bible_versions", lambda _: {"kjv": BibleVersion(id="kjv", path=kjv_db)}
    )
    application = startup(monkeypatch, tmp_path, bible_db)
    with TestClient(application):
        state = application.state.app_state
        assert state.active_bible_version() == "kjv"
        assert state.current_display.verse_start == 18
        assert state.current_display.text


@pytest.mark.asyncio
async def test_runtime_write_failure_is_background_only(tmp_path, caplog):
    parent = tmp_path / "not-a-directory"
    parent.write_text("blocked")
    store = RuntimeStateStore(parent / "runtime_state.json")
    store.record(reference(), "sample")
    await store.flush()
    assert "Could not persist settings" in caplog.text
    assert store.snapshot.current.book == "John"


def test_restore_translation_mismatch_keeps_history(monkeypatch, tmp_path, bible_db, caplog):
    import logging

    recovery_file(tmp_path, translation="another_translation")
    with caplog.at_level(logging.INFO):
        application = startup(monkeypatch, tmp_path, bible_db)
    with TestClient(application) as client:
        state = application.state.app_state
        assert state.current_display is None
        assert len(state.reference_history) == 1
        assert state.reference_history[0].text
        with client.websocket_connect("/ws/display") as display:
            assert display.receive_json()["type"] == "clear"
    assert "saved translation" in caplog.text


@pytest.mark.asyncio
async def test_bookkeeping_failures_do_not_prevent_display_or_clear(
    tmp_path, bible_db, monkeypatch, caplog
):
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", "")
    state = AppState(BibleLookup(str(bible_db)), SettingsStore(tmp_path / "settings.json"))
    state.connection_manager.send_to_display = AsyncMock()
    monkeypatch.setattr(state.runtime_state, "record", Mock(side_effect=OSError("record")))
    monkeypatch.setattr(state.runtime_state, "clear", Mock(side_effect=OSError("clear")))
    monkeypatch.setattr(state.service_log, "emit", Mock(side_effect=OSError("log")))
    ref = reference()
    state.pending_references.append(ref)
    assert await state.approve_pending(ref.id) is ref
    assert (
        state.connection_manager.send_to_display.await_args_list[0].args[0]["type"] == "reference"
    )
    await state.clear_display()
    assert state.connection_manager.send_to_display.await_args_list[1].args[0]["type"] == "clear"
    assert state.current_display is None
    assert "Could not record runtime" in caplog.text
    assert "Could not record cleared" in caplog.text
    await state.shutdown()


@pytest.mark.asyncio
async def test_translation_lookup_failure_does_not_prevent_display(tmp_path, bible_db, monkeypatch):
    state = AppState(BibleLookup(str(bible_db)), SettingsStore(tmp_path / "settings.json"))
    state.connection_manager.send_to_display = AsyncMock()
    monkeypatch.setattr(state, "active_bible_version", Mock(side_effect=OSError("metadata")))
    ref = reference()
    state.pending_references.append(ref)
    assert await state.approve_pending(ref.id) is ref
    assert state.connection_manager.send_to_display.await_count == 1
    await state.shutdown()


@pytest.mark.asyncio
async def test_settings_broadcast_starts_before_persistence(tmp_path, bible_db, monkeypatch):
    state = AppState(BibleLookup(str(bible_db)), SettingsStore(tmp_path / "settings.json"))
    calls = []

    async def send():
        calls.append("send")

    monkeypatch.setattr(state, "send_display_settings", send)
    monkeypatch.setattr(state.settings_store, "save_verse_limit", lambda _: calls.append("save"))
    monkeypatch.setattr(state.settings_store, "save_theme", lambda _: calls.append("save"))
    monkeypatch.setattr(state.settings_store, "save_clear_mode", lambda _: calls.append("save"))
    await state.set_verse_limit(5)
    await state.set_display_theme("light")
    await state.set_clear_mode("blank")
    assert calls == ["send", "save", "send", "save", "send", "save"]
    await state.shutdown()


@pytest.mark.parametrize("value", [3.0, 5.0, 10.0, True, False, "3", None])
def test_save_verse_limit_rejects_invalid_types(tmp_path, value):
    assert not SettingsStore(tmp_path / "settings.json").save_verse_limit(value)


def test_invalid_settings_warn_but_missing_are_silent(tmp_path, caplog):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    assert store.load_verse_limit() == 3
    assert store.load_theme() == "dark"
    assert not caplog.records
    path.write_text('{"verse_limit": 4, "theme": "blue"}')
    assert store.load_verse_limit() == 3
    assert store.load_theme() == "dark"
    assert "invalid verse limit" in caplog.text
    assert "invalid display theme" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_closes_database_if_flush_raises(tmp_path, bible_db, monkeypatch):
    state = AppState(BibleLookup(str(bible_db)), SettingsStore(tmp_path / "settings.json"))
    monkeypatch.setattr(state.runtime_state, "flush", AsyncMock(side_effect=OSError("flush")))
    stop = Mock(wraps=state.service_log.stop)
    close = Mock(wraps=state.bible_lookup.close)
    monkeypatch.setattr(state.service_log, "stop", stop)
    monkeypatch.setattr(state.bible_lookup, "close", close)
    await state.shutdown()
    stop.assert_called_once()
    close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["cancel", "timeout", "error"])
async def test_shutdown_flushes_before_connection_cleanup_and_always_closes_resources(
    tmp_path, bible_db, monkeypatch, caplog, outcome
):
    state = AppState(BibleLookup(str(bible_db)), SettingsStore(tmp_path / "settings.json"))
    flush = AsyncMock()
    monkeypatch.setattr(state.runtime_state, "flush", flush)
    monkeypatch.setattr(server_state, "SEND_TIMEOUT", 0.05)
    started = asyncio.Event()

    async def close_connections():
        flush.assert_awaited_once()
        started.set()
        if outcome == "error":
            raise RuntimeError("connection cleanup failed")
        await asyncio.Event().wait()

    monkeypatch.setattr(state.connection_manager, "close", close_connections)
    stop = Mock(wraps=state.service_log.stop)
    close = Mock(wraps=state.bible_lookup.close)
    monkeypatch.setattr(state.service_log, "stop", stop)
    monkeypatch.setattr(state.bible_lookup, "close", close)
    task = asyncio.create_task(state.shutdown())
    await asyncio.wait_for(started.wait(), 1)
    if outcome == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif outcome == "error":
        with pytest.raises(RuntimeError, match="connection cleanup failed"):
            await task
    else:
        await asyncio.wait_for(task, 1)
        assert "Timed out closing WebSocket connections" in caplog.text
    stop.assert_called_once()
    close.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_timeboxes_flush_and_stalled_service_log(
    tmp_path, bible_db, monkeypatch, caplog
):
    state = AppState(BibleLookup(str(bible_db)), SettingsStore(tmp_path / "settings.json"))
    release = threading.Event()
    task = asyncio.create_task(asyncio.Event().wait())
    state.runtime_state._task = task
    monkeypatch.setattr(state.service_log, "stop", lambda: release.wait(10))
    close = Mock(wraps=state.bible_lookup.close)
    monkeypatch.setattr(state.bible_lookup, "close", close)
    try:
        await asyncio.wait_for(state.shutdown(), timeout=5)
        close.assert_called_once()
        assert "Timed out flushing runtime state" in caplog.text
        assert "Timed out stopping service log" in caplog.text
    finally:
        release.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["verse_limit", "theme", "clear_mode"])
async def test_concurrent_settings_saves_keep_latest_value(tmp_path, bible_db, monkeypatch, kind):
    state = AppState(BibleLookup(str(bible_db)), SettingsStore(tmp_path / "settings.json"))
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def send():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()

    monkeypatch.setattr(state, "send_display_settings", send)
    setter = {
        "verse_limit": state.set_verse_limit,
        "theme": state.set_display_theme,
        "clear_mode": state.set_clear_mode,
    }[kind]
    first, second = {
        "verse_limit": (5, 10),
        "theme": ("light", "dark"),
        "clear_mode": ("blank", "idle"),
    }[kind]
    first_task = asyncio.create_task(setter(first))
    await started.wait()
    await setter(second)
    release.set()
    await first_task
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved[kind] == second
    await state.shutdown()


def test_mismatched_restore_stays_blank_after_switching_back(
    monkeypatch, tmp_path, bible_db, kjv_db
):
    first = startup(monkeypatch, tmp_path, kjv_db)
    with TestClient(first) as client, client.websocket_connect("/ws/operator") as operator:
        assert operator.receive_json()["type"] == "state_snapshot"
        operator.send_json(
            {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 16}
        )
        assert operator.receive_json()["type"] == "display_confirmed"
    path = tmp_path / "runtime_state.json"
    assert json.loads(path.read_text())["current"]["translation"] == "kjv"

    second = startup(monkeypatch, tmp_path, bible_db)
    assert second.state.app_state.current_display is None
    assert len(second.state.app_state.reference_history) == 1
    assert json.loads(path.read_text())["current"] is None  # Written before startup returns.
    with TestClient(second):
        pass

    third = startup(monkeypatch, tmp_path, kjv_db)
    with TestClient(third) as client, client.websocket_connect("/ws/display") as display:
        assert third.state.app_state.current_display is None
        assert display.receive_json()["type"] == "clear"
        assert len(third.state.app_state.reference_history) == 1


def test_stalled_runtime_disk_writer_does_not_block_process_exit(tmp_path):
    script = """
import asyncio
import sys
import threading
from pathlib import Path
from core.server.models import BibleReference
from core.server.runtime_state import RuntimeStateStore

async def main():
    store = RuntimeStateStore(Path(sys.argv[1]))
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    def blocked_write(data):
        loop.call_soon_threadsafe(started.set)
        threading.Event().wait(60)
    store._writer.write_json = blocked_write
    store.record(BibleReference(book='John', chapter=3, verse_start=16), 'kjv')
    await asyncio.wait_for(started.wait(), 5)
    await store.flush()

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "runtime.json")],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Timed out flushing runtime state" in result.stderr
