#!/usr/bin/env python3
"""Test authentication and authorization for operator endpoints."""

import json

import pytest
from fastapi.testclient import TestClient

from core.biblelive_server import app
from core.config import config


@pytest.fixture
def client():
    """Create a test client (state reset via conftest autouse fixture)."""
    return TestClient(app)


@pytest.fixture
def auth_enabled():
    """Enable authentication for the duration of a test."""
    original_require = config.REQUIRE_AUTH
    original_token = config.OPERATOR_TOKEN

    config.REQUIRE_AUTH = True
    config.OPERATOR_TOKEN = "test-token-12345"

    yield "test-token-12345"

    config.REQUIRE_AUTH = original_require
    config.OPERATOR_TOKEN = original_token


def test_websocket_operator_without_auth_disabled(client):
    """Operator WebSocket works without auth when auth is disabled."""
    assert config.REQUIRE_AUTH is False
    with client.websocket_connect("/ws/operator") as websocket:
        message = json.loads(websocket.receive_text())
        assert message["type"] == "state_snapshot"


def test_websocket_operator_rejected_without_token(client, auth_enabled):
    """Operator WebSocket is rejected without token when auth is enabled."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/operator") as websocket:
        websocket.receive_text()


def test_websocket_operator_accepted_with_valid_token(client, auth_enabled):
    """Operator WebSocket is accepted with a valid token."""
    token = auth_enabled
    with client.websocket_connect(f"/ws/operator?token={token}") as websocket:
        message = json.loads(websocket.receive_text())
        assert message["type"] == "state_snapshot"


def test_http_approve_rejected_without_token(client, auth_enabled):
    """HTTP approve is rejected with 401 when auth is enabled and no token sent."""
    ref = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", "inferred": False}
    submit_response = client.post("/api/reference", json=ref)
    reference_id = submit_response.json()["reference_id"]

    response = client.post("/api/approve", json={"reference_id": reference_id})
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_http_approve_rejected_with_invalid_token(client, auth_enabled):
    """HTTP approve is rejected with 401 for a wrong token."""
    ref = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", "inferred": False}
    reference_id = client.post("/api/reference", json=ref).json()["reference_id"]

    response = client.post(
        "/api/approve",
        json={"reference_id": reference_id},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_http_approve_accepted_with_valid_token(client, auth_enabled):
    """HTTP approve is accepted with a valid token."""
    token = auth_enabled
    ref = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", "inferred": False}
    reference_id = client.post("/api/reference", json=ref).json()["reference_id"]

    response = client.post(
        "/api/approve",
        json={"reference_id": reference_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_state_requires_auth(client, auth_enabled):
    """GET /api/state requires auth when enabled."""
    response = client.get("/api/state")
    assert response.status_code == 401

    token = auth_enabled
    response = client.get("/api/state", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "pending" in response.json()


def test_display_websocket_always_public(client, auth_enabled):
    """Display WebSocket is always public (no auth required)."""
    with client.websocket_connect("/ws/display") as websocket:
        message = json.loads(websocket.receive_text())
        assert message["type"] == "clear"


def test_api_reference_always_public(client, auth_enabled):
    """POST /api/reference is always public (no auth for ingestion)."""
    ref = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", "inferred": False}
    response = client.post("/api/reference", json=ref)
    assert response.status_code == 201
    assert "reference_id" in response.json()
