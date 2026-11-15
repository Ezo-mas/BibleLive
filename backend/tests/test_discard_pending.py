"""Discarding a queued detection only changes the operators' pending queue."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from core.config import config
from core.server.models import BibleReference
from core.server.service_log import ServiceLog


def test_discard_broadcasts_to_two_operators_without_touching_display(client, app_state):
    removed = BibleReference(book="John", chapter=3, verse_start=16)
    kept = BibleReference(book="John", chapter=3, verse_start=17)
    queue = app_state.pending_references
    queue.extend([removed, kept])
    with (
        client.websocket_connect("/ws/display") as display,
        client.websocket_connect("/ws/operator") as first,
        client.websocket_connect("/ws/operator") as second,
    ):
        assert display.receive_json()["type"] == "clear"
        assert display.receive_json()["type"] == "display_settings"
        for operator in (first, second):
            assert operator.receive_json()["type"] == "state_snapshot"

        first.send_json({"action": "discard_pending", "reference_id": removed.id})
        for operator in (first, second):
            update = operator.receive_json()
            assert update["type"] == "pending_update"
            assert update["data"]["count"] == 1
            assert [ref["id"] for ref in update["data"]["pending"]] == [kept.id]
        assert app_state.pending_references is queue
        assert queue == [kept]
        assert app_state.current_display is None
        assert app_state.reference_history == []

        second.send_json({"action": "approve", "reference_id": removed.id})
        assert second.receive_json() == {
            "type": "error",
            "data": {
                "action": "approve",
                "message": "Reference not found or already processed",
            },
        }
        # A later successful approval fences every stream: discard sent no display
        # message, no extra operator confirmation, and the error stayed with its sender.
        second.send_json({"action": "approve", "reference_id": kept.id})
        for operator in (first, second):
            assert operator.receive_json()["type"] == "approval_confirmed"
            assert operator.receive_json() == {
                "type": "pending_update",
                "data": {"pending": [], "count": 0},
            }
        shown = display.receive_json()
        assert shown["type"] == "reference"
        assert shown["data"]["id"] == kept.id


def test_unknown_discard_errors_only_to_sender_without_broadcast(client, app_state, monkeypatch):
    operator_send = AsyncMock(wraps=app_state.connection_manager.send_to_operator)
    display_send = AsyncMock(wraps=app_state.connection_manager.send_to_display)
    with (
        client.websocket_connect("/ws/display") as display,
        client.websocket_connect("/ws/operator") as first,
        client.websocket_connect("/ws/operator") as second,
    ):
        assert display.receive_json()["type"] == "clear"
        assert display.receive_json()["type"] == "display_settings"
        assert first.receive_json()["type"] == "state_snapshot"
        assert second.receive_json()["type"] == "state_snapshot"
        monkeypatch.setattr(app_state.connection_manager, "send_to_operator", operator_send)
        monkeypatch.setattr(app_state.connection_manager, "send_to_display", display_send)
        first.send_json({"action": "discard_pending", "reference_id": "unknown"})
        assert first.receive_json() == {
            "type": "error",
            "data": {
                "action": "discard_pending",
                "message": "Reference not found or already processed",
            },
        }
        operator_send.assert_not_awaited()
        display_send.assert_not_awaited()
        # Fence the other operator's stream without a timeout or a blocking read.
        first.send_json({"action": "clear"})
        assert first.receive_json()["type"] == "clear_confirmed"
        assert second.receive_json()["type"] == "clear_confirmed"
        assert display.receive_json()["type"] == "clear"


@pytest.mark.parametrize("payload", [{}, {"reference_id": None}, {"reference_id": []}])
def test_discard_validates_reference_id(client, app_state, monkeypatch, payload):
    discard = AsyncMock()
    monkeypatch.setattr(app_state, "discard_pending", discard)
    with client.websocket_connect("/ws/operator") as operator:
        assert operator.receive_json()["type"] == "state_snapshot"
        operator.send_json({"action": "discard_pending", **payload})
        error = operator.receive_json()
        assert error["type"] == "error"
        assert error["data"]["action"] == "discard_pending"
        assert "reference_id" in error["data"]["message"]
        discard.assert_not_awaited()


def test_discard_unexpected_error_uses_existing_envelope(client, app_state, monkeypatch):
    monkeypatch.setattr(app_state, "discard_pending", AsyncMock(side_effect=RuntimeError("boom")))
    with client.websocket_connect("/ws/operator") as operator:
        assert operator.receive_json()["type"] == "state_snapshot"
        operator.send_json({"action": "discard_pending", "reference_id": "example"})
        assert operator.receive_json() == {
            "type": "error",
            "data": {
                "action": "discard_pending",
                "message": "Internal error discarding pending reference",
            },
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["discard", "approve"])
async def test_discard_and_approve_race_has_one_winner(app_state, monkeypatch, winner):
    ref = BibleReference(book="John", chapter=3, verse_start=16)
    app_state.pending_references.append(ref)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_send(*_):
        entered.set()
        await release.wait()

    display_send = AsyncMock(side_effect=hold_send if winner == "approve" else None)
    monkeypatch.setattr(app_state, "_send_display_reference", display_send)
    monkeypatch.setattr(
        app_state,
        "_send_pending_update",
        AsyncMock(side_effect=hold_send if winner == "discard" else None),
    )
    first = app_state.discard_pending if winner == "discard" else app_state.approve_pending
    task = asyncio.create_task(first(ref.id))
    try:
        await entered.wait()
        assert app_state.pending_references == []
        if winner == "discard":
            assert await app_state.approve_pending(ref.id) is None
            assert app_state.current_display is None
            display_send.assert_not_awaited()
        else:
            assert await app_state.discard_pending(ref.id) is False
            assert app_state.current_display is ref
            display_send.assert_awaited_once_with(ref)
    finally:
        release.set()
        result = await task
    assert result is (True if winner == "discard" else ref)
    assert len(app_state.reference_history) == (0 if winner == "discard" else 1)


@pytest.mark.asyncio
async def test_discard_during_auto_approval_arrival_update(app_state, monkeypatch):
    app_state.auto_mode = "high"
    ref = BibleReference(book="John", chapter=3, verse_start=16, confidence="high")
    entered = asyncio.Event()
    release = asyncio.Event()
    send_update = app_state._send_pending_update

    async def hold_arrival():
        await send_update()
        if not entered.is_set():
            entered.set()
            await release.wait()

    monkeypatch.setattr(app_state, "_send_pending_update", hold_arrival)
    display_send = AsyncMock()
    monkeypatch.setattr(app_state.connection_manager, "send_to_display", display_send)
    task = asyncio.create_task(app_state.add_reference(ref))
    try:
        await entered.wait()
        assert await app_state.discard_pending(ref.id) is True
    finally:
        release.set()
        await task
    assert app_state.pending_references == []
    assert app_state._auto_approval_ids == set()
    assert app_state.current_display is None
    assert app_state.reference_history == []
    display_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_discard_after_auto_approval_pops_reference(app_state, monkeypatch):
    app_state.auto_mode = "high"
    ref = BibleReference(book="John", chapter=3, verse_start=16, confidence="high")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_display(_ref):
        entered.set()
        await release.wait()

    monkeypatch.setattr(app_state, "_send_display_reference", hold_display)
    task = asyncio.create_task(app_state.add_reference(ref))
    try:
        await entered.wait()
        assert ref.id in app_state._auto_approval_ids
        assert await app_state.discard_pending(ref.id) is False
    finally:
        release.set()
        await task
    assert app_state.pending_references == []
    assert app_state._auto_approval_ids == set()
    assert app_state.current_display is ref
    assert app_state.reference_history == [ref]


@pytest.mark.asyncio
async def test_discard_emits_coordinate_only_service_event(app_state, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SERVICE_LOG_DIR", str(tmp_path))
    log = ServiceLog()
    monkeypatch.setattr(app_state, "service_log", log)
    ref = BibleReference(book="John", chapter=3, verse_start=16, verse_end=17, text="SECRET")
    app_state.pending_references.append(ref)
    try:
        assert await app_state.discard_pending(ref.id) is True
        assert await app_state.discard_pending(ref.id) is False
        # The event allowlist must also reject accidentally supplied text.
        log.emit("pending_discarded", text="SECRET", transcript="SECRET")
    finally:
        log.stop()
    raw = next(tmp_path.glob("*.jsonl")).read_text()
    assert "SECRET" not in raw
    event, filtered = [json.loads(line) for line in raw.splitlines()]
    assert event.pop("ts")
    assert event == {
        "event": "pending_discarded",
        "reference_id": ref.id,
        "book": "John",
        "chapter": 3,
        "verse_start": 16,
        "verse_end": 17,
    }
    assert set(filtered) == {"ts", "event"}
