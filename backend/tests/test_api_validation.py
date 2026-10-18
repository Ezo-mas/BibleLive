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
    """P2-1: inputs exactly at the size limits are still accepted.

    The longest real chapter number (Psalm 150) and a 1000-character ``text``
    pass the size checks; the text itself is ignored (see the ingest tests below).
    """
    response = client.post(
        "/api/reference",
        json={
            "book": "Psalms",
            "chapter": 150,
            "verse_start": 1,
            "confidence": "high",
            "text": "x" * 1000,
        },
    )
    assert response.status_code == 201
    assert "reference_id" in response.json()


# --- Ingest validation: only real scripture can enter the queue ----------------


def _submit(client, **fields):
    body = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", **fields}
    return client.post("/api/reference", json=body)


def test_reference_submit_rejects_unknown_book(client, app_state):
    response = _submit(client, book="URGENT NOTICE", chapter=1, verse_start=1)
    assert response.status_code == 404
    assert "Unknown book" in response.json()["detail"]
    assert app_state.pending_references == []


def test_reference_submit_rejects_chapters_and_verses_not_in_the_bible(client, app_state):
    assert _submit(client, chapter=22, verse_start=1).status_code == 422  # John has 21
    assert _submit(client, chapter=3, verse_start=99).status_code == 404  # John 3 has 36
    assert app_state.pending_references == []


def test_reference_submit_rejects_malformed_ranges(client, app_state):
    assert _submit(client, verse_start=16, verse_end=2).status_code == 422
    assert _submit(client, verse_start=None, verse_end=5).status_code == 422
    assert app_state.pending_references == []


def test_reference_submit_accepts_chapter_only_and_canonicalizes_book(client, app_state):
    assert _submit(client, book="  john ", verse_start=None).status_code == 201
    assert app_state.pending_references[0].book == "John"
    assert app_state.pending_references[0].verse_start is None


def test_reference_submit_discards_caller_text(client, app_state):
    """Submitted text never reaches the queue or the display; the DB's does."""
    with client.websocket_connect("/ws/display") as display:
        display.receive_json()  # initial clear
        display.receive_json()  # display settings

        reference_id = _submit(client, text="Words that are not scripture").json()["reference_id"]
        # The queued preview is the database's text too, never the caller's.
        assert app_state.pending_references[0].text.startswith("For God so loved the world")

        assert client.post("/api/approve", json={"reference_id": reference_id}).status_code == 200
        shown = display.receive_json()["data"]

    assert "not scripture" not in shown["text"]
    assert shown["text"].startswith("For God so loved the world")
    assert [v["verse"] for v in shown["verses"]] == [16]


def test_queued_detection_carries_its_verse_text(client, app_state):
    """The operator can read a detection before approving it (Up Next preview)."""
    assert _submit(client, verse_start=16, verse_end=17).status_code == 201
    queued = app_state.pending_references[0]
    assert [v.verse for v in queued.verses] == [16, 17]
    assert queued.text.startswith("For God so loved the world")


def test_queued_chapter_only_detection_keeps_its_label(client, app_state):
    """The preview shows the verse approval would show; the label stays the chapter."""
    assert _submit(client, verse_start=None).status_code == 201
    queued = app_state.pending_references[0]
    assert (queued.verse_start, queued.verse_end) == (None, None)
    assert [v.verse for v in queued.verses] == [1]


def test_range_running_past_the_chapter_is_trimmed_in_the_label(client):
    """John 3 ends at verse 36, so "John 3:30-40" is shown as 3:30-36."""
    reference_id = _submit(client, verse_start=30, verse_end=40).json()["reference_id"]
    shown = client.post("/api/approve", json={"reference_id": reference_id}).json()["reference"]
    assert shown["verse_end"] == 36
    assert [v["verse"] for v in shown["verses"]] == list(range(30, 37))


def test_auto_approve_cannot_display_a_rejected_submission(client, app_state):
    """With auto-approve on, a bad submission still never reaches a projector."""
    app_state.auto_mode = "all"
    with client.websocket_connect("/ws/display") as display:
        display.receive_json()  # initial clear
        display.receive_json()  # display settings
        assert _submit(client, book="Announcements", chapter=1, verse_start=1).status_code == 404
        assert app_state.current_display is None

        # A valid one is still auto-approved, showing database text.
        assert _submit(client, text="injected").status_code == 201
        shown = display.receive_json()
    assert shown["type"] == "reference"
    assert shown["data"]["book"] == "John"
    assert "injected" not in shown["data"]["text"]


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
