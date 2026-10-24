"""Tests for ConnectionManager send reliability (P1-2).

A stalled or half-open client must not block sends to healthy clients, and a
socket whose send fails must be pruned so future sends and connection counts
stay truthful.
"""

import asyncio

from core.server import connections
from core.server.connections import ConnectionManager


class _FakeWebSocket:
    """Minimal WebSocket stand-in for exercising ConnectionManager."""

    def __init__(self, *, fail: bool = False, delay: float = 0.0):
        self.sent: list[str] = []
        self.fail = fail
        self.delay = delay
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("socket is dead")
        self.sent.append(data)


async def _make_manager(*sockets):
    cm = ConnectionManager()
    for socket in sockets:
        await cm.connect(socket, "display")
    return cm


def test_dead_socket_pruned():
    """A socket that raises on send is removed from the connection sets."""

    async def scenario():
        dead = _FakeWebSocket(fail=True)
        healthy = _FakeWebSocket()
        cm = await _make_manager(dead, healthy)

        failed = await cm.send_to_display({"type": "reference", "data": {}})
        return cm, dead, healthy, failed

    cm, dead, healthy, failed = asyncio.run(scenario())

    assert failed == [dead]
    assert dead not in cm.display_connections
    # The healthy client still receives the message and stays registered.
    assert healthy in cm.display_connections
    assert len(healthy.sent) == 1


def test_stalled_socket_times_out_and_is_pruned(monkeypatch):
    """A send that exceeds SEND_TIMEOUT is abandoned and the socket pruned."""
    monkeypatch.setattr(connections, "SEND_TIMEOUT", 0.01)

    async def scenario():
        stalled = _FakeWebSocket(delay=1.0)
        healthy = _FakeWebSocket()
        cm = await _make_manager(stalled, healthy)

        failed = await cm.send_to_display({"type": "reference", "data": {}})
        return cm, stalled, healthy, failed

    cm, stalled, healthy, failed = asyncio.run(scenario())

    assert failed == [stalled]
    assert stalled not in cm.display_connections
    assert healthy in cm.display_connections
    assert len(healthy.sent) == 1


def test_pruned_socket_no_longer_blocks_later_sends():
    """After pruning, a later send only targets live sockets."""

    async def scenario():
        dead = _FakeWebSocket(fail=True)
        healthy = _FakeWebSocket()
        cm = await _make_manager(dead, healthy)

        await cm.send_to_display({"type": "reference", "data": {}})
        # The second send must not invoke the dead socket again.
        failed_again = await cm.send_to_display({"type": "clear", "data": {}})
        return cm, dead, healthy, failed_again

    cm, dead, healthy, failed_again = asyncio.run(scenario())

    assert failed_again == []
    assert len(healthy.sent) == 2
    assert len(cm.display_connections) == 1
