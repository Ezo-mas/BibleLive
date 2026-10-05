"""Test API schema validation and error handling."""

import json

# NOTE: the ``client`` and ``app_state`` fixtures come from tests/conftest.py.
# conftest's autouse ``reset_app_state`` fixture already resets AppState before
# each test, so no local reset fixture is needed here.


def test_reference_submit_validation(client):
    """Test that reference submission validates input"""
    # Valid reference
    valid_ref = {
        "book": "John",
        "chapter": 3,
        "verse_start": 16,
        "confidence": "high",
        "inferred": False,
    }
    response = client.post("/api/reference", json=valid_ref)
    assert response.status_code == 201
    assert "reference_id" in response.json()

    # Missing required field
    invalid_ref = {
        "book": "John",
        "chapter": 3,
        # missing confidence
    }
    response = client.post("/api/reference", json=invalid_ref)
    assert response.status_code == 422

    # Invalid confidence value
    invalid_ref = {"book": "John", "chapter": 3, "confidence": "invalid", "inferred": False}
    response = client.post("/api/reference", json=invalid_ref)
    assert response.status_code == 422


def test_approve_validation(client):
    """Test approval endpoint validates reference_id"""
    # Submit a reference first
    ref = {
        "book": "Psalms",
        "chapter": 23,
        "verse_start": 1,
        "confidence": "high",
        "inferred": False,
    }
    submit_response = client.post("/api/reference", json=ref)
    ref_id = submit_response.json()["reference_id"]

    # Valid approval
    response = client.post("/api/approve", json={"reference_id": ref_id})
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Missing reference_id
    response = client.post("/api/approve", json={})
    assert response.status_code == 422

    # Non-existent reference_id
    response = client.post("/api/approve", json={"reference_id": "fake-uuid"})
    assert response.status_code == 404


def test_api_returns_proper_status_codes(client):
    """Test that API returns semantically correct status codes"""
    # 201 for creation
    ref = {
        "book": "Genesis",
        "chapter": 1,
        "verse_start": 1,
        "confidence": "high",
        "inferred": False,
    }
    response = client.post("/api/reference", json=ref)
    assert response.status_code == 201

    # 200 for successful operations
    response = client.post("/api/clear")
    assert response.status_code == 200

    response = client.post("/api/clear-pending")
    assert response.status_code == 200

    response = client.get("/api/state")
    assert response.status_code == 200


def test_websocket_validates_messages(client):
    """Test WebSocket validates incoming messages"""
    with client.websocket_connect("/ws/operator") as websocket:
        # Consume state snapshot
        websocket.receive_text()

        # Invalid JSON: server sends an error and keeps the connection open
        websocket.send_text("{invalid json}")
        response = websocket.receive_text()
        msg = json.loads(response)
        assert msg["type"] == "error"

        # Missing action field
        websocket.send_text(json.dumps({}))
        response = websocket.receive_text()
        msg = json.loads(response)
        assert msg["type"] == "error"
        assert "action" in msg["data"]["message"].lower()


def test_websocket_handles_unknown_actions(client):
    """Test WebSocket rejects unknown actions"""
    with client.websocket_connect("/ws/operator") as websocket:
        # Consume state snapshot
        websocket.receive_text()

        # Unknown action
        websocket.send_text(json.dumps({"action": "invalid_action"}))
        response = websocket.receive_text()
        msg = json.loads(response)
        assert msg["type"] == "error"
        assert "unknown" in msg["data"]["message"].lower()


def test_reference_submit_rejects_oversized_payloads(client):
    """P2-1: oversized book/text/chapter payloads are rejected before queueing.

    ``/api/reference`` is unauthenticated, so a LAN client must not be able to
    push multi-MB fields into the pending queue.
    """
    base = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high"}

    # book over 100 chars
    assert client.post("/api/reference", json={**base, "book": "A" * 101}).status_code == 422
    # text over 1000 chars
    assert client.post("/api/reference", json={**base, "text": "x" * 1001}).status_code == 422
    # chapter over 200
    assert client.post("/api/reference", json={**base, "chapter": 201}).status_code == 422

    # Nothing rejected above was queued.
    assert client.get("/api/state").json()["pending"] == []


def test_reference_submit_accepts_values_at_the_limits(client):
    """P2-1: inputs exactly at the configured limits are still accepted."""
    response = client.post(
        "/api/reference",
        json={
            "book": "A" * 100,
            "chapter": 200,
            "verse_start": 1,
            "confidence": "high",
            "text": "x" * 1000,
        },
    )
    assert response.status_code == 201
    assert "reference_id" in response.json()


def test_api_response_models(client):
    """Test that API responses match defined models"""
    # Submit reference
    ref = {"book": "John", "chapter": 1, "verse_start": 1, "confidence": "high", "inferred": False}
    response = client.post("/api/reference", json=ref)
    data = response.json()
    assert "status" in data
    assert "message" in data
    assert "reference_id" in data
    assert data["status"] == "success"

    # Get state
    response = client.get("/api/state")
    data = response.json()
    assert "current_display" in data
    assert "pending" in data
    assert "history" in data
    assert "pending_count" in data
    assert "history_count" in data
    assert "display_connections" in data
    assert "operator_connections" in data
