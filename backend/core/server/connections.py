"""WebSocket connection manager."""

import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Bound each WebSocket send so one stalled client (projector asleep, half-open
# TCP) cannot block the caller -- and therefore every other client -- forever.
SEND_TIMEOUT = 5.0


class ConnectionManager:
    """Manage WebSocket connections for display and operator clients."""

    def __init__(self):
        """Initialize the connection manager with empty connection sets."""
        self.active_connections: list[WebSocket] = []
        self.display_connections: set[WebSocket] = set()
        self.operator_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket, client_type: str = "display"):
        """Accept a websocket connection and register it based on client type.

        Args:
            websocket: The WebSocket connection.
            client_type: Either "display" or "operator". Defaults to "display".
        """
        await websocket.accept()
        self.active_connections.append(websocket)
        if client_type == "display":
            self.display_connections.add(websocket)
        elif client_type == "operator":
            self.operator_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        """Remove a websocket from all connection sets.

        Args:
            websocket: The WebSocket connection to remove.
        """
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.display_connections.discard(websocket)
        self.operator_connections.discard(websocket)

    async def _send_to_connections(self, message: dict, connections) -> list[WebSocket]:
        """Send a message to a set/list of connections.

        Each send is bounded by ``SEND_TIMEOUT`` and inspected: a socket whose
        send times out or raises is logged and pruned from the connection sets
        so a dead client cannot block future broadcasts or inflate connection
        counts. Returns the list of sockets that failed.
        """
        # Copy before iterating: pruning a failed socket mutates the sets below,
        # which is not safe while iterating them.
        connections = list(connections)
        if not connections:
            return []

        payload = json.dumps(message)
        results = await asyncio.gather(
            *[
                asyncio.wait_for(connection.send_text(payload), timeout=SEND_TIMEOUT)
                for connection in connections
            ],
            return_exceptions=True,
        )

        failed: list[WebSocket] = []
        for connection, result in zip(connections, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.TimeoutError):
                    logger.warning(
                        f"WebSocket send timed out after {SEND_TIMEOUT}s; pruning dead connection"
                    )
                else:
                    logger.warning(f"Failed to send WebSocket message: {result}")
                failed.append(connection)
                self.disconnect(connection)
        return failed

    async def _safe_send(self, websocket: WebSocket, message: dict):
        """Safely send a message to a single websocket, logging errors."""
        try:
            await websocket.send_text(json.dumps(message))
        except Exception as e:
            logger.warning(f"Failed to send WebSocket message: {e}")

    async def send_to_display(self, message: dict) -> list[WebSocket]:
        """Send message to all display connections; returns failed sockets."""
        return await self._send_to_connections(message, self.display_connections)

    async def send_to_operator(self, message: dict) -> list[WebSocket]:
        """Send message to all operator connections; returns failed sockets."""
        return await self._send_to_connections(message, self.operator_connections)

    async def broadcast(self, message: dict):
        """Send message to all connections"""
        await self._send_to_connections(message, self.active_connections)
