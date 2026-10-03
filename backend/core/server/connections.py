"""WebSocket connection manager."""

import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


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

    async def _send_to_connections(self, message: dict, connections):
        """Send message to a set/list of connections"""
        if connections:
            await asyncio.gather(
                *[connection.send_text(json.dumps(message)) for connection in connections],
                return_exceptions=True,
            )

    async def _safe_send(self, websocket: WebSocket, message: dict):
        """Safely send a message to a single websocket, logging errors."""
        try:
            await websocket.send_text(json.dumps(message))
        except Exception as e:
            logger.warning(f"Failed to send WebSocket message: {e}")

    async def send_to_display(self, message: dict):
        """Send message to all display connections"""
        await self._send_to_connections(message, self.display_connections)

    async def send_to_operator(self, message: dict):
        """Send message to all operator connections"""
        await self._send_to_connections(message, self.operator_connections)

    async def broadcast(self, message: dict):
        """Send message to all connections"""
        await self._send_to_connections(message, self.active_connections)
