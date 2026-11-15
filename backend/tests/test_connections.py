"""Tests for ConnectionManager send reliability (P1-2).

A stalled or half-open client must not block sends to healthy clients, and a
socket whose send fails must be pruned so future sends and connection sets
reflect the removal.
"""

import asyncio
import json

import pytest

from core.server import connections
from core.server.connections import ConnectionManager
from core.server.models import BibleReference
from core.server.ws import _ws_error


class _FakeWebSocket:
    """Minimal WebSocket stand-in for exercising ConnectionManager."""

    def __init__(self, *, fail: bool = False, block: bool = False, close_mode: str = "ok"):
        self.sent: list[str] = []
        self.fail = fail
        self.block = block
        self.close_mode = close_mode
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.close_started = asyncio.Event()
        self.send_calls = 0
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        self.send_calls += 1
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("socket is dead")
        self.sent.append(data)

    async def close(self) -> None:
        self.close_started.set()
        if self.close_mode == "block":
            await asyncio.Event().wait()
        if self.close_mode == "raise":
            raise RuntimeError("close failed")
        self.closed.set()


async def _make_manager(*sockets):
    cm = ConnectionManager()
    for socket in sockets:
        await cm.connect(socket, "display")
    return cm


async def _drain(cm):
    """Wait for delivery only in tests that inspect fake sockets' sent messages."""
    await asyncio.wait_for(asyncio.gather(*(q.join() for q in cm._queues.values())), 1)


@pytest.mark.parametrize("single_socket", [False, True])
def test_dead_socket_pruned(single_socket):
    """Delivery failures prune asynchronously, not in the enqueue result."""

    async def scenario():
        dead = _FakeWebSocket(fail=True)
        healthy = _FakeWebSocket()
        cm = await _make_manager(dead, healthy)
        try:
            message = {"type": "reference", "data": {}}
            if single_socket:
                await cm._safe_send(dead, message)
                await cm._safe_send(healthy, message)
            else:
                assert await cm.send_to_display(message) == []
            await asyncio.wait_for(dead.closed.wait(), 1)
            await _drain(cm)
            assert dead not in cm.display_connections
            assert healthy in cm.display_connections
            assert len(healthy.sent) == 1
        finally:
            await cm.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("single_socket", [False, True])
def test_stalled_socket_times_out_and_is_pruned(monkeypatch, single_socket):
    """A stalled send is pruned and a best-effort close is attempted."""
    monkeypatch.setattr(connections, "SEND_TIMEOUT", 0.01)

    async def scenario():
        stalled = _FakeWebSocket(block=True)
        healthy = _FakeWebSocket()
        cm = await _make_manager(stalled, healthy)
        try:
            message = {"type": "reference", "data": {}}
            if single_socket:
                await cm._safe_send(stalled, message)
                await cm._safe_send(healthy, message)
            else:
                assert await cm.send_to_display(message) == []
            await asyncio.wait_for(stalled.closed.wait(), 1)
            await _drain(cm)
            assert stalled not in cm.display_connections
            assert healthy in cm.display_connections
            assert len(healthy.sent) == 1
        finally:
            await cm.close()

    asyncio.run(scenario())


def test_pruned_socket_no_longer_blocks_later_sends():
    """After pruning, later broadcasts and single-socket sends drop dead sockets."""

    async def scenario():
        dead = _FakeWebSocket(fail=True)
        healthy = _FakeWebSocket()
        cm = await _make_manager(dead, healthy)
        try:
            await cm.send_to_display({"type": "reference", "data": {}})
            await asyncio.wait_for(dead.closed.wait(), 1)
            failed_again = await cm.send_to_display({"type": "clear", "data": {}})
            await cm._safe_send(dead, {"type": "clear", "data": {}})
            await _drain(cm)
            assert failed_again == []
            assert len(healthy.sent) == 2
            assert len(cm.display_connections) == 1
            assert dead.send_calls == 1
        finally:
            await cm.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("single_socket", [False, True])
@pytest.mark.parametrize("client_type", ["display", "operator"])
@pytest.mark.parametrize("in_flight", [False, True])
def test_full_queue_prunes(monkeypatch, single_socket, client_type, in_flight):
    """Overflow prunes immediately, before writer startup or during a blocked send."""
    monkeypatch.setattr(connections, "SEND_QUEUE_MAX", 2)

    async def scenario():
        socket = _FakeWebSocket(block=True)
        healthy = _FakeWebSocket()
        cm = ConnectionManager()
        await cm.connect(socket, client_type)
        await cm.connect(healthy, client_type)
        writer = cm._writers[socket]
        queue = cm._queues[socket]
        if in_flight:
            await cm._safe_send(socket, {"i": -1})
            await asyncio.wait_for(socket.started.wait(), 1)
        for i in range(2):
            await cm._safe_send(socket, {"i": i})
        if single_socket:
            await cm._safe_send(socket, {"i": 2})
        else:
            broadcast = getattr(cm, f"send_to_{client_type}")
            assert await broadcast({"i": 2}) == [socket]
        assert socket not in cm.display_connections
        assert socket not in cm.operator_connections
        await asyncio.wait_for(socket.closed.wait(), 1)
        await _drain(cm)
        assert healthy.sent == ([] if single_socket else [json.dumps({"i": 2})])
        await cm.close()
        await asyncio.wait_for(queue.join(), 1)
        assert writer.done()
        assert not cm._tasks
        assert socket.sent == []

    asyncio.run(scenario())


@pytest.mark.parametrize("close_mode", ["block", "raise"])
def test_prune_close_is_bounded_and_errors_logged(monkeypatch, caplog, close_mode):
    monkeypatch.setattr(connections, "SEND_TIMEOUT", 0.01)

    async def scenario():
        socket = _FakeWebSocket(fail=True, close_mode=close_mode)
        cm = await _make_manager(socket)
        await cm.send_to_display({"type": "clear", "data": {}})
        await asyncio.wait_for(socket.close_started.wait(), 1)
        await asyncio.wait_for(cm.close(), 0.5)
        assert socket not in cm.display_connections
        assert not cm._tasks

    asyncio.run(scenario())
    assert "Failed to close pruned WebSocket" in caplog.text


@pytest.mark.parametrize("in_flight", [False, True])
def test_disconnect_stops_writer_and_drops_queue(in_flight):
    async def scenario():
        socket = _FakeWebSocket(block=True)
        cm = await _make_manager(socket)
        writer = cm._writers[socket]
        queue = cm._queues[socket]
        await cm._safe_send(socket, {"i": 0})
        await cm._safe_send(socket, {"i": 1})
        if in_flight:
            await asyncio.wait_for(socket.started.wait(), 1)
        cm.disconnect(socket)
        await asyncio.gather(writer, return_exceptions=True)
        await asyncio.wait_for(queue.join(), 1)
        await cm._safe_send(socket, {"i": 2})
        assert writer.done()
        assert not cm._tasks
        assert not cm._queues
        assert not cm._writers
        assert not cm.display_connections
        assert socket.sent == []

    asyncio.run(scenario())


def test_unregistered_single_socket_send_is_dropped():
    async def scenario():
        socket = _FakeWebSocket()
        await ConnectionManager()._safe_send(socket, {"type": "clear", "data": {}})
        assert socket.sent == []
        assert socket.send_calls == 0
        assert not socket.accepted

    asyncio.run(scenario())


def test_approval_confirmed_while_display_stalled(app_state, monkeypatch):
    monkeypatch.setattr(connections, "SEND_TIMEOUT", 0.5)

    async def scenario():
        cm = app_state.connection_manager
        stalled = _FakeWebSocket(block=True)
        operator = _FakeWebSocket()
        await cm.connect(stalled, "display")
        await cm.connect(operator, "operator")
        ref = BibleReference(book="John", chapter=3, verse_start=16, confidence="high")
        app_state.pending_references.append(ref)
        try:
            started = asyncio.get_running_loop().time()
            assert await app_state.approve_and_notify(ref.id) is ref
            await asyncio.wait_for(cm._queues[operator].join(), 0.25)
            assert asyncio.get_running_loop().time() - started < 0.25
            messages = [json.loads(payload) for payload in operator.sent]
            assert [m["type"] for m in messages] == ["approval_confirmed", "pending_update"]
            assert messages[0]["data"]["reference"] == ref.model_dump()
            assert messages[1]["data"]["pending"] == []
            assert stalled in cm.display_connections
            await asyncio.wait_for(stalled.closed.wait(), 1)
            assert stalled not in cm.display_connections
            assert not stalled.sent
            assert app_state.current_display is ref
        finally:
            await cm.close()
        assert not cm._tasks

    asyncio.run(scenario())


def test_mixed_messages_preserve_per_socket_order():
    async def scenario():
        cm = ConnectionManager()
        displays = [_FakeWebSocket(), _FakeWebSocket()]
        operators = [_FakeWebSocket(), _FakeWebSocket()]
        expected = {socket: [] for socket in displays + operators}
        for socket in displays:
            await cm.connect(socket, "display")
        for socket in operators:
            await cm.connect(socket, "operator")
        try:
            for i in range(1000):
                message = {"type": "test", "data": {"i": i}}
                if i % 4 == 0:
                    recipients = displays
                    assert await cm.send_to_display(message) == []
                elif i % 4 == 1:
                    recipients = operators
                    assert await cm.send_to_operator(message) == []
                else:
                    recipients = [(displays if i % 4 == 2 else operators)[i % 7 % 2]]
                    await cm._safe_send(recipients[0], message)
                for socket in recipients:
                    expected[socket].append(json.dumps(message))
                if i % 25 == 0:
                    await _drain(cm)
            await _drain(cm)
            for socket, messages in expected.items():
                assert socket.sent == messages
            assert cm.display_connections == set(displays)
            assert cm.operator_connections == set(operators)
        finally:
            await cm.close()
        assert not cm._tasks

    asyncio.run(scenario())


def test_ws_error_follows_queued_broadcast(app_state):
    async def scenario():
        cm = app_state.connection_manager
        socket = _FakeWebSocket(block=True)
        await cm.connect(socket, "operator")
        try:
            broadcast = {"type": "pending_update", "data": {"pending": [], "count": 0}}
            await cm.send_to_operator(broadcast)
            await asyncio.wait_for(socket.started.wait(), 1)
            await _ws_error(socket, app_state, "approve", "Missing reference_id")
            socket.release.set()
            await _drain(cm)
            assert socket.sent == [
                json.dumps(broadcast),
                json.dumps(
                    {
                        "type": "error",
                        "data": {"message": "Missing reference_id", "action": "approve"},
                    }
                ),
            ]
        finally:
            await cm.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("client_type", ["display", "operator"])
def test_connect_state_precedes_broadcast(app_state, client_type):
    async def scenario():
        cm = app_state.connection_manager
        socket = _FakeWebSocket()
        await cm.connect(socket, client_type)
        try:
            if client_type == "display":
                await app_state.send_display_current_state(socket)
                await cm.send_to_display({"type": "later"})
                expected = ["clear", "display_settings", "later"]
            else:
                await app_state.send_operator_state_snapshot(socket)
                await cm.send_to_operator({"type": "later"})
                expected = ["state_snapshot", "later"]
            await _drain(cm)
            assert [json.loads(m)["type"] for m in socket.sent] == expected
        finally:
            await cm.close()

    asyncio.run(scenario())


def test_testclient_exit_stops_old_manager_writers(app, app_state):
    from fastapi.testclient import TestClient

    old_manager = app_state.connection_manager
    with TestClient(app) as client, client.websocket_connect("/ws/display") as display:
        assert display.receive_json()["type"] == "clear"
        with client.websocket_connect("/ws/operator") as operator:
            assert operator.receive_json()["type"] == "state_snapshot"
            writers = list(old_manager._writers.values())
            assert len(writers) == 2
    # Same manager replacement as reset_app_state: no tasks can remain on it.
    app_state.connection_manager = ConnectionManager()
    assert all(writer.done() for writer in writers)
    assert not old_manager._tasks
    assert not old_manager._queues
    assert not old_manager.display_connections
    assert not old_manager.operator_connections
