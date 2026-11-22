"""The server keeps one timeout for the current on-air passage."""

import asyncio
import json
from queue import Queue
from unittest.mock import AsyncMock, Mock

import pytest

from core.passage import resolve_passage
from core.server import display_session
from core.server.settings_store import SettingsStore


@pytest.fixture
def timer_expirations(monkeypatch):
    started: Queue[tuple[int, asyncio.Future[None]]] = Queue()

    async def wait_for_expiry(seconds: int) -> None:
        expiry = asyncio.get_running_loop().create_future()
        started.put((seconds, expiry))
        await expiry

    monkeypatch.setattr(display_session, "sleep", wait_for_expiry)
    return started


def test_an_approval_replaces_the_old_deadline_and_expiration_notifies_every_socket(
    client, app_state, monkeypatch, timer_expirations
):
    clear = AsyncMock(wraps=app_state.clear_and_notify)
    emit = Mock(wraps=app_state.service_log.emit)
    monkeypatch.setattr(app_state, "clear_and_notify", clear)
    monkeypatch.setattr(app_state.service_log, "emit", emit)
    with (
        client.websocket_connect("/ws/display") as display,
        client.websocket_connect("/ws/operator") as first,
        client.websocket_connect("/ws/operator") as second,
    ):
        assert display.receive_json()["type"] == "clear"
        assert display.receive_json()["data"]["auto_clear_seconds"] is None
        for operator in (first, second):
            snapshot = operator.receive_json()
            assert snapshot["data"]["display_settings"]["auto_clear_seconds"] is None

        first.send_json({"action": "set_auto_clear", "seconds": 5})
        for operator in (first, second):
            confirmed = operator.receive_json()
            assert confirmed["type"] == "display_settings_confirmed"
            assert confirmed["data"]["auto_clear_seconds"] == 5
        assert display.receive_json() == {"type": "display_settings", "data": confirmed["data"]}
        assert SettingsStore(app_state.settings_store.path).load_auto_clear_seconds() == 5

        first.send_json(
            {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 16}
        )
        for operator in (first, second):
            assert operator.receive_json()["type"] == "display_confirmed"
        assert display.receive_json()["data"]["verse_start"] == 16
        seconds, first_expiry = timer_expirations.get(timeout=5)
        assert seconds == 5

        queued = client.post(
            "/api/reference",
            json={"book": "John", "chapter": 3, "verse_start": 17, "confidence": "high"},
        )
        assert queued.status_code == 201
        for operator in (first, second):
            assert operator.receive_json()["type"] == "pending_update"
        first.send_json({"action": "approve", "reference_id": queued.json()["reference_id"]})
        for operator in (first, second):
            assert operator.receive_json()["type"] == "approval_confirmed"
            assert operator.receive_json()["type"] == "pending_update"
        assert display.receive_json()["data"]["verse_start"] == 17
        seconds, expiry = timer_expirations.get(timeout=5)
        assert seconds == 5
        assert first_expiry.cancelled()
        assert client.portal is not None
        client.portal.call(expiry.set_result, None)
        assert display.receive_json() == {"type": "clear", "data": {}}
        for operator in (first, second):
            assert operator.receive_json() == {"type": "clear_confirmed", "data": {}}
        clear.assert_awaited_once_with(automatic=True)
        emit.assert_any_call("auto_cleared")
        snapshot = client.get("/api/state").json()
        assert snapshot["current_display"] is None
        assert snapshot["history_count"] == 2


def test_auto_clear_rejects_invalid_values_and_persists_off(client, app_state):
    with client.websocket_connect("/ws/operator") as operator:
        operator.receive_json()
        operator.send_json({"action": "set_auto_clear", "seconds": 600})
        assert operator.receive_json()["data"]["auto_clear_seconds"] == 600
        for payload in ({}, *({"seconds": value} for value in (4, 601, True, "60", 60.5))):
            operator.send_json({"action": "set_auto_clear", **payload})
            error = operator.receive_json()
            assert error["type"] == "error"
            assert error["data"]["action"] == "set_auto_clear"
        assert client.get("/api/state").json()["display_settings"]["auto_clear_seconds"] == 600
        operator.send_json({"action": "set_auto_clear", "seconds": None})
        assert operator.receive_json()["data"]["auto_clear_seconds"] is None
        assert SettingsStore(app_state.settings_store.path).load_auto_clear_seconds() is None


def test_turning_off_cancels_the_timer_even_if_the_settings_cannot_be_saved(
    client, app_state, monkeypatch, timer_expirations
):
    with client.websocket_connect("/ws/operator") as operator:
        operator.receive_json()
        operator.send_json({"action": "set_auto_clear", "seconds": 5})
        assert operator.receive_json()["data"]["auto_clear_seconds"] == 5
        operator.send_json(
            {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 16}
        )
        assert operator.receive_json()["type"] == "display_confirmed"
        _, expiry = timer_expirations.get(timeout=5)

        monkeypatch.setattr(app_state.settings_store, "write_json", lambda _: False)
        operator.send_json({"action": "set_auto_clear", "seconds": None})
        confirmed = operator.receive_json()
        assert confirmed["type"] == "display_settings_confirmed"
        assert confirmed["data"]["auto_clear_seconds"] is None
        error = operator.receive_json()
        assert error["type"] == "error"
        assert error["data"]["action"] == "set_auto_clear"
        assert "applied for this session" in error["data"]["message"]

        assert expiry.cancelled()
        snapshot = client.get("/api/state").json()
        assert snapshot["current_display"]["verse_start"] == 16
        assert snapshot["display_settings"]["auto_clear_seconds"] is None
        assert SettingsStore(app_state.settings_store.path).load_auto_clear_seconds() == 5


@pytest.mark.parametrize("saved", [4, 601, True, "60", 60.5, [], {}])
def test_invalid_saved_auto_clear_duration_defaults_to_off(tmp_path, saved):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"auto_clear_seconds": saved}), encoding="utf-8")
    assert SettingsStore(path).load_auto_clear_seconds() is None


@pytest.mark.asyncio
async def test_auto_clear_logs_task_failures(app_state, monkeypatch, caplog):
    started: asyncio.Queue[asyncio.Task] = asyncio.Queue()

    async def expire(_seconds: int) -> None:
        task = asyncio.current_task()
        assert task is not None
        started.put_nowait(task)

    clear = AsyncMock(side_effect=RuntimeError("clear failed unexpectedly"))
    monkeypatch.setattr(app_state, "clear_and_notify", clear)
    monkeypatch.setattr(display_session, "sleep", expire)
    assert await app_state.set_auto_clear(60)
    passage = resolve_passage(app_state.bible_lookup, "John", 3, 16)
    await app_state.display_manual_passage(passage)

    await (await started.get())
    clear.assert_awaited_once_with(automatic=True)
    assert "Could not auto-clear the display" in caplog.text
    assert "clear failed unexpectedly" in caplog.text


@pytest.mark.asyncio
async def test_off_air_and_manual_clear_cancel_the_timer(app_state, monkeypatch):
    started: asyncio.Queue[tuple[int, asyncio.Task]] = asyncio.Queue()

    async def wait_for_expiry(seconds: int) -> None:
        timer = asyncio.current_task()
        assert timer is not None
        started.put_nowait((seconds, timer))
        await asyncio.Future()

    monkeypatch.setattr(display_session, "sleep", wait_for_expiry)
    assert await app_state.set_auto_clear(60)
    passage = resolve_passage(app_state.bible_lookup, "John", 3, 16)
    await app_state.display_manual_passage(passage)
    seconds, timer = await started.get()
    assert seconds == 60

    await app_state.set_live(False)
    with pytest.raises(asyncio.CancelledError):
        await timer
    followed = await app_state.display_manual_passage(passage, source="follow")
    await asyncio.sleep(0)
    assert started.empty()
    assert app_state.current_display is followed

    await app_state.set_live(True)
    seconds, timer = await started.get()
    assert seconds == 60
    assert await app_state.set_auto_clear(45)
    with pytest.raises(asyncio.CancelledError):
        await timer
    seconds, timer = await started.get()
    assert seconds == 45
    assert await app_state.set_auto_clear(None)
    with pytest.raises(asyncio.CancelledError):
        await timer
    assert app_state.current_display is followed

    assert await app_state.set_auto_clear(45)
    _, timer = await started.get()
    await app_state.clear_and_notify()
    with pytest.raises(asyncio.CancelledError):
        await timer
    assert app_state.current_display is None
