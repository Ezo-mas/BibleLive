#!/usr/bin/env python3
"""Test WebSocket endpoints and approval flows for BibleLive."""

import json

import pytest
from fastapi.testclient import TestClient

from core.biblelive_server import app


@pytest.fixture
def client():
    """Create a test client (state reset via conftest autouse fixture)."""
    return TestClient(app)


def _submit_reference(client, book="John", chapter=3, verse_start=16):
    """Submit a reference via HTTP and return its reference_id."""
    reference = {
        "book": book,
        "chapter": chapter,
        "verse_start": verse_start,
        "confidence": "high",
        "inferred": False,
        "text": "",
    }
    response = client.post("/api/reference", json=reference)
    assert response.status_code == 201
    return response.json()["reference_id"]


def test_websocket_display_connection(client):
    """A display client connects and receives the initial empty state."""
    with client.websocket_connect("/ws/display") as websocket:
        message = json.loads(websocket.receive_text())
        assert message["type"] == "clear"


def test_websocket_operator_connection(client):
    """An operator client connects and receives a state snapshot."""
    with client.websocket_connect("/ws/operator") as websocket:
        message = json.loads(websocket.receive_text())
        assert message["type"] == "state_snapshot"
        assert message["data"]["pending"] == []


def test_websocket_operator_send_approve(client):
    """Operator approves a pending reference over WebSocket."""
    reference_id = _submit_reference(client)

    with client.websocket_connect("/ws/operator") as websocket:
        initial_message = json.loads(websocket.receive_text())
        assert initial_message["type"] == "state_snapshot"
        assert len(initial_message["data"]["pending"]) == 1

        websocket.send_text(json.dumps({"action": "approve", "reference_id": reference_id}))

        # Deterministic order: confirmation first, then the updated queue.
        confirmed = json.loads(websocket.receive_text())
        assert confirmed["type"] == "approval_confirmed"
        assert confirmed["data"]["reference"]["book"] == "John"
        assert confirmed["data"]["reference"]["chapter"] == 3
        assert confirmed["data"]["reference"]["verse_start"] == 16

        pending_message = json.loads(websocket.receive_text())
        assert pending_message["type"] == "pending_update"
        assert pending_message["data"]["count"] == 0


def test_websocket_display_receive_reference(client):
    """Display client receives the reference when an operator approves it."""
    reference_id = _submit_reference(client, book="Psalms", chapter=23, verse_start=1)

    with (
        client.websocket_connect("/ws/display") as display_ws,
        client.websocket_connect("/ws/operator") as operator_ws,
    ):
        # Display receives its initial (empty) state.
        assert json.loads(display_ws.receive_text())["type"] == "clear"
        # Operator receives the state snapshot.
        assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

        operator_ws.send_text(json.dumps({"action": "approve", "reference_id": reference_id}))

        assert json.loads(operator_ws.receive_text())["type"] == "approval_confirmed"
        assert json.loads(operator_ws.receive_text())["type"] == "pending_update"

        display_msg = json.loads(display_ws.receive_text())
        assert display_msg["type"] == "reference"
        assert display_msg["data"]["book"] == "Psalms"
        assert display_msg["data"]["chapter"] == 23
        assert display_msg["data"]["verse_start"] == 1


def test_websocket_operator_clear_display(client):
    """Operator clears the display over WebSocket."""
    reference_id = _submit_reference(client, book="Genesis", chapter=1, verse_start=1)

    with (
        client.websocket_connect("/ws/display") as display_ws,
        client.websocket_connect("/ws/operator") as websocket,
    ):
        assert json.loads(display_ws.receive_text())["type"] == "clear"
        assert json.loads(websocket.receive_text())["type"] == "state_snapshot"

        websocket.send_text(json.dumps({"action": "approve", "reference_id": reference_id}))
        assert json.loads(websocket.receive_text())["type"] == "approval_confirmed"
        assert json.loads(websocket.receive_text())["type"] == "pending_update"
        assert json.loads(display_ws.receive_text())["type"] == "reference"

        websocket.send_text(json.dumps({"action": "clear"}))
        assert json.loads(websocket.receive_text())["type"] == "clear_confirmed"

        clear_msg = json.loads(display_ws.receive_text())
        assert clear_msg["type"] == "clear"


def test_websocket_operator_clear_pending(client):
    """Operator clears the pending queue over WebSocket."""
    _submit_reference(client, book="Genesis", chapter=1, verse_start=1)
    _submit_reference(client, book="Genesis", chapter=1, verse_start=2)

    with client.websocket_connect("/ws/operator") as websocket:
        snapshot = json.loads(websocket.receive_text())
        assert snapshot["type"] == "state_snapshot"
        assert len(snapshot["data"]["pending"]) == 2

        websocket.send_text(json.dumps({"action": "clear_pending"}))

        # Deterministic order: confirmation first, then the empty queue.
        assert json.loads(websocket.receive_text())["type"] == "pending_cleared"
        pending_message = json.loads(websocket.receive_text())
        assert pending_message["type"] == "pending_update"
        assert pending_message["data"]["count"] == 0


def test_websocket_invalid_approve_reference_id(client):
    """Approving an unknown reference_id returns an error, not a confirmation."""
    _submit_reference(client, book="Exodus", chapter=20, verse_start=1)

    with client.websocket_connect("/ws/operator") as websocket:
        assert json.loads(websocket.receive_text())["type"] == "state_snapshot"

        websocket.send_text(json.dumps({"action": "approve", "reference_id": "invalid-ref-id-xyz"}))
        error = json.loads(websocket.receive_text())
        assert error["type"] == "error"
        assert error["data"]["action"] == "approve"
        assert "not found" in error["data"]["message"].lower()

        # The connection stays usable: a valid approve still works.
        state = client.get("/api/state").json()
        valid_id = state["pending"][0]["id"]
        websocket.send_text(json.dumps({"action": "approve", "reference_id": valid_id}))
        assert json.loads(websocket.receive_text())["type"] == "approval_confirmed"
        assert json.loads(websocket.receive_text())["type"] == "pending_update"


def test_http_api_reference_submission(client):
    """Submitting a reference via HTTP returns 201 with a reference_id."""
    response = client.post(
        "/api/reference",
        json={
            "book": "Leviticus",
            "chapter": 19,
            "verse_start": 18,
            "confidence": "high",
            "inferred": False,
            "text": "",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "success"
    assert "reference_id" in data


def test_http_api_approve_reference(client):
    """Approving a reference via HTTP updates display and state."""
    reference_id = _submit_reference(client, book="Numbers", chapter=6, verse_start=24)

    response = client.post("/api/approve", json={"reference_id": reference_id})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["message"] == "Reference approved and displayed"
    assert data["reference"]["book"] == "Numbers"
    assert data["reference"]["chapter"] == 6
    assert data["reference"]["verse_start"] == 24


def test_http_api_clear_display(client):
    """Clearing the display via HTTP returns success."""
    reference_id = _submit_reference(client, book="Deuteronomy", chapter=6, verse_start=4)
    assert client.post("/api/approve", json={"reference_id": reference_id}).status_code == 200

    response = client.post("/api/clear")
    assert response.status_code == 200
    assert response.json()["message"] == "Display cleared"


def test_http_api_clear_pending(client):
    """Clearing the pending queue via HTTP returns success with a count."""
    _submit_reference(client, book="Joshua", chapter=1, verse_start=8)
    _submit_reference(client, book="Joshua", chapter=1, verse_start=9)

    response = client.post("/api/clear-pending")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["message"] == "Cleared 2 pending references"


def test_get_app_state(client):
    """GET /api/state returns the full state snapshot shape."""
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    for key in (
        "current_display",
        "pending",
        "pending_count",
        "history_count",
        "display_connections",
        "operator_connections",
    ):
        assert key in data
