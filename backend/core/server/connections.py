"""WebSocket connection manager."""

import asyncio
import json
import logging
from collections.abc import Iterable
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Bound sends and best-effort closes in each socket's background writer/cleanup.
SEND_TIMEOUT = 5.0
# The largest normal synchronous burst is a translation change (3 operator
# messages); approval sends 2, snapshots at most 2 (including a follow-along hint).
# Leave a wide margin because put_nowait cannot drain until the caller yields.
SEND_QUEUE_MAX = 256


class ConnectionManager:
    """Manage ordered outbound queues for display and operator clients.

    All sends to registered sockets, including private single-socket sends, are
    queued without waiting for delivery. Unregistered sockets (before connect
    or after disconnect/prune) silently drop sends; authentication sends nothing
    before registration. Broadcast results list only synchronous queue overflows,
    not failures discovered later by writers. A best-effort close may also
    time out if the peer has stopped reading; the socket's endpoint task (when
    one was registered) is then cancelled so its receive loop stops, and
    ``take_abandoned`` tells the endpoint that cancellation was this one.
    """

    def __init__(self) -> None:
        """Initialize the connection manager with empty connection sets."""
        self.display_connections: set[WebSocket] = set()
        self.operator_connections: set[WebSocket] = set()
        self._queues: dict[WebSocket, asyncio.Queue[str]] = {}
        self._writers: dict[WebSocket, asyncio.Task[None]] = {}
        self._endpoints: dict[WebSocket, asyncio.Task[Any]] = {}
        self._pruned_endpoints: dict[WebSocket, asyncio.Task[Any]] = {}
        self._abandoned: set[WebSocket] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    async def connect(
        self,
        websocket: WebSocket,
        client_type: str = "display",
        accepted: bool = False,
        endpoint: asyncio.Task[Any] | None = None,
    ) -> None:
        """Accept a websocket connection and register it based on client type.

        Args:
            websocket: The WebSocket connection.
            client_type: Either "display" or "operator". Defaults to "display".
            accepted: True when the caller already accepted the socket (an
                operator socket is accepted before its token is checked).
            endpoint: The task running this socket's receive loop. If the socket
                is pruned and its close fails, this task is cancelled.
        """
        if not accepted:
            await websocket.accept()
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=SEND_QUEUE_MAX)
        self._queues[websocket] = queue
        writer = asyncio.create_task(self._write(websocket, queue))
        self._writers[websocket] = writer
        self._track(writer)
        if endpoint is not None:
            self._endpoints[websocket] = endpoint
        if client_type == "display":
            self.display_connections.add(websocket)
        elif client_type == "operator":
            self.operator_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a websocket, drop queued messages and stop its writer.

        Args:
            websocket: The WebSocket connection to remove.
        """
        self.display_connections.discard(websocket)
        self.operator_connections.discard(websocket)
        self._endpoints.pop(websocket, None)
        self._pruned_endpoints.pop(websocket, None)
        self._abandoned.discard(websocket)
        queue = self._queues.pop(websocket, None)
        if queue is not None:
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()
        writer = self._writers.pop(websocket, None)
        if writer is not None and writer is not asyncio.current_task():
            writer.cancel()

    def _track(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        """Stop all writers and await any bounded prune/close cleanup."""
        for websocket in list(self._queues):
            self.disconnect(websocket)
        if self._tasks:
            tasks = list(self._tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.difference_update(tasks)

    def take_abandoned(self, websocket: WebSocket) -> bool:
        """Return True once if this manager cancelled the socket's endpoint task.

        The endpoint calls this when it sees ``CancelledError``: True means the
        socket was pruned and could not be closed, so the endpoint should end as
        if the client had disconnected; False means some other cancellation
        (such as server shutdown) that must propagate.
        """
        if websocket in self._abandoned:
            self._abandoned.discard(websocket)
            return True
        return False

    def _prune(self, websocket: WebSocket) -> None:
        writer = self._writers.get(websocket)
        endpoint = self._endpoints.get(websocket)
        self.disconnect(websocket)
        if endpoint is not None:
            # Kept until the endpoint's own disconnect (its receive loop ended),
            # so a failed close never cancels an endpoint that is already ending.
            self._pruned_endpoints[websocket] = endpoint
        self._track(asyncio.create_task(self._close_socket(websocket, writer)))

    async def _close_socket(self, websocket: WebSocket, writer: asyncio.Task[None] | None) -> None:
        # Never close concurrently with an in-flight send, including a writer
        # cancelled before it first ran (whose finally block would not execute).
        if writer is not None:
            await asyncio.gather(writer, return_exceptions=True)
        try:
            await asyncio.wait_for(websocket.close(), timeout=SEND_TIMEOUT)
        except Exception as e:
            logger.warning(f"Failed to close pruned WebSocket: {e!r}")
            # The peer is not reading, so its receive loop would otherwise run
            # until the TCP connection ends: stop it as a disconnect.
            endpoint = self._pruned_endpoints.pop(websocket, None)
            if endpoint is not None and not endpoint.done():
                self._abandoned.add(websocket)
                endpoint.cancel()

    async def _write(self, websocket: WebSocket, queue: asyncio.Queue[str]) -> None:
        try:
            while True:
                payload = await queue.get()
                try:
                    await asyncio.wait_for(websocket.send_text(payload), timeout=SEND_TIMEOUT)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Failed to send WebSocket message; pruning connection: {e!r}")
            self._prune(websocket)

    def _enqueue(self, websocket: WebSocket, payload: str) -> bool:
        queue = self._queues.get(websocket)
        if queue is None:
            return False
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("WebSocket send queue full; pruning connection")
            self._prune(websocket)
            return True
        return False

    async def _send_to_connections(
        self, message: dict, connections: Iterable[WebSocket]
    ) -> list[WebSocket]:
        """Enqueue a broadcast; return sockets pruned now because their queue is full."""
        # Copy before iterating: pruning a failed socket mutates the sets below,
        # which is not safe while iterating them.
        connections = list(connections)
        if not connections:
            return []

        payload = json.dumps(message)
        return [connection for connection in connections if self._enqueue(connection, payload)]

    async def _safe_send(self, websocket: WebSocket, message: dict) -> None:
        """Enqueue for one registered socket; silently drop after disconnect/prune."""
        if websocket in self._queues:
            self._enqueue(websocket, json.dumps(message))

    async def send_to_display(self, message: dict) -> list[WebSocket]:
        """Enqueue for displays; return only sockets pruned now on queue overflow."""
        return await self._send_to_connections(message, self.display_connections)

    async def send_to_operator(self, message: dict) -> list[WebSocket]:
        """Enqueue for operators; return only sockets pruned now on queue overflow."""
        return await self._send_to_connections(message, self.operator_connections)
