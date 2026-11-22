"""The server keeps one timeout for the current on-air passage."""

import asyncio
from queue import Queue

import pytest

from core.passage import resolve_passage
from core.server import display_session
from core.server.settings_store import SettingsStore


def test_auto_clear_replaces_the_old_deadline_and_notifies_every_socket(
    client, app_state, monkeypatch
):
    started: Queue[tuple[int, asyncio.Future[None]]] = Queue()

    async def wait_for_expiry(seconds: int) -> None:
        expiry = asyncio.get_running_loop().create_future()
        started.put((seconds, expiry))
        await expiry

    monkeypatch.setattr(display_session, "sleep", wait_for_expiry)
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

        def show(verse: int) -> asyncio.Future[None]:
            first.send_json(
                {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": verse}
            )
            for operator in (first, second):
                assert operator.receive_json()["type"] == "display_confirmed"
            assert display.receive_json()["data"]["verse_start"] == verse
            seconds, expiry = started.get(timeout=5)
            assert seconds == 5
            return expiry

        first_expiry = show(16)
        expiry = show(17)
        assert first_expiry.cancelled()
        assert client.portal is not None
        client.portal.call(expiry.set_result, None)
        assert display.receive_json() == {"type": "clear", "data": {}}
        for operator in (first, second):
            assert operator.receive_json() == {"type": "clear_confirmed", "data": {}}
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
