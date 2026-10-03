"""Test the reference ID-based approval protocol."""

import json


def test_reference_has_id_and_timestamp(client):
    """New references get an ID and timestamp."""
    reference = {
        "book": "John",
        "chapter": 3,
        "verse_start": 16,
        "verse_end": 16,
        "confidence": "high",
        "inferred": False,
        "text": "",
    }
    response = client.post("/api/reference", json=reference)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "success"
    assert "reference_id" in data

    # Verify the reference in pending queue has ID
    state = client.get("/api/state").json()
    assert len(state["pending"]) == 1
    assert "id" in state["pending"][0]
    assert "created_at" in state["pending"][0]


def test_approve_by_reference_id(client):
    """Approving a reference returns the same reference ID."""
    reference = {
        "book": "Romans",
        "chapter": 8,
        "verse_start": 28,
        "confidence": "high",
        "inferred": False,
    }
    submit_response = client.post("/api/reference", json=reference)
    reference_id = submit_response.json()["reference_id"]

    approve_response = client.post("/api/approve", json={"reference_id": reference_id})
    assert approve_response.status_code == 200
    assert approve_response.json()["reference"]["id"] == reference_id


def test_approve_nonexistent_reference_id(client):
    """Approving an unknown ID returns 404."""
    response = client.post("/api/approve", json={"reference_id": "nonexistent-id"})
    assert response.status_code == 404


def test_websocket_state_snapshot(client):
    """Operator gets a full state snapshot on connection."""
    ref = {"book": "John", "chapter": 1, "verse_start": 1, "confidence": "high", "inferred": False}
    client.post("/api/reference", json=ref)

    with client.websocket_connect("/ws/operator") as websocket:
        data = websocket.receive_text()
        message = json.loads(data)
        assert message["type"] == "state_snapshot"
        assert "pending" in message["data"]
        assert len(message["data"]["pending"]) == 1
