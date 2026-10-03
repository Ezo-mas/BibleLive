"""Test WebSocket endpoints and approval flows for BibleLive."""

import json

# NOTE: the ``client`` fixture (bound to the app built by the ``app`` fixture)
# comes from tests/conftest.py; the autouse ``reset_app_state`` fixture there
# resets AppState between tests.


def _submit_reference(client, book="John", chapter=3, verse_start=16, verse_end=None):
    """Submit a reference via HTTP and return its reference_id."""
    reference = {
        "book": book,
        "chapter": chapter,
        "verse_start": verse_start,
        "confidence": "high",
        "inferred": False,
        "text": "",
    }
    if verse_end is not None:
        reference["verse_end"] = verse_end
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
        # Display receives its initial (empty) state, then its settings.
        assert json.loads(display_ws.receive_text())["type"] == "clear"
        assert json.loads(display_ws.receive_text())["type"] == "display_settings"
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
        assert json.loads(display_ws.receive_text())["type"] == "display_settings"
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


def _approve(client, **kwargs):
    """Submit and approve a reference, returning the approved payload."""
    reference_id = _submit_reference(client, **kwargs)
    response = client.post("/api/approve", json={"reference_id": reference_id})
    assert response.status_code == 200
    return response.json()["reference"]


class TestApprovedReferenceText:
    """Approved references carry verse numbers and cleaned text for display."""

    def test_includes_numbered_verses(self, client):
        ref = _approve(client, book="John", chapter=3, verse_start=16)
        assert ref["verses"] == [
            {
                "verse": 16,
                "text": "For God so loved the world, that he gave his only begotten Son, "
                "that whosoever believeth in him should not perish, but have everlasting life.",
            }
        ]

    def test_text_matches_joined_verses(self, client):
        ref = _approve(client, book="John", chapter=3, verse_start=16)
        assert ref["text"] == " ".join(v["text"] for v in ref["verses"])

    def test_text_has_no_editorial_markup(self, client):
        ref = _approve(client, book="Genesis", chapter=1, verse_start=2)
        assert "{" not in ref["text"] and "[" not in ref["text"]

    def test_range_returns_each_verse(self, client):
        ref = _approve(client, book="John", chapter=3, verse_start=16, verse_end=18)
        assert [v["verse"] for v in ref["verses"]] == [16, 17, 18]
        assert ref["verse_end"] == 18

    def test_chapter_only_reference_is_anchored_to_verse_one(self, client):
        """``Luke 1`` must resolve to text, not show a bare reference label."""
        response = client.post(
            "/api/reference",
            json={
                "book": "Luke",
                "chapter": 1,
                "verse_start": None,
                "confidence": "high",
                "inferred": False,
                "text": "",
            },
        )
        assert response.status_code == 201
        reference_id = response.json()["reference_id"]

        approved = client.post("/api/approve", json={"reference_id": reference_id}).json()
        ref = approved["reference"]
        assert ref["verse_start"] == 1
        assert ref["verses"][0]["verse"] == 1
        assert ref["text"].startswith("Forasmuch as many have taken in hand")

    def test_large_range_is_capped(self, client):
        """A whole-chapter request must not send an unbounded payload."""
        from core.config import config

        ref = _approve(client, book="Psalms", chapter=119, verse_start=1, verse_end=176)
        assert ref["verse_end"] == config.MAX_VERSES_PER_REFERENCE
        assert ref["verses"][-1]["verse"] == config.MAX_VERSES_PER_REFERENCE
        assert len(ref["verses"]) == config.MAX_VERSES_PER_REFERENCE


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


class TestDisplaySettings:
    """Display settings are operator-owned and pushed to the display page."""

    def test_display_receives_settings_on_connect(self, client):
        """A display gets its verse limit right after its content state."""
        with client.websocket_connect("/ws/display") as display_ws:
            assert json.loads(display_ws.receive_text())["type"] == "clear"
            settings = json.loads(display_ws.receive_text())
            assert settings["type"] == "display_settings"
            assert settings["data"]["verse_limit"] == 3
            # Projectors run dark by default.
            assert settings["data"]["theme"] == "dark"

    def test_operator_set_verse_limit_reaches_display(self, client):
        """Changing the limit in the console updates every connected display."""
        with (
            client.websocket_connect("/ws/display") as display_ws,
            client.websocket_connect("/ws/operator") as operator_ws,
        ):
            assert json.loads(display_ws.receive_text())["type"] == "clear"
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

            operator_ws.send_text(json.dumps({"action": "set_verse_limit", "verse_limit": 10}))

            confirmed = json.loads(operator_ws.receive_text())
            assert confirmed["type"] == "display_settings_confirmed"
            assert confirmed["data"]["verse_limit"] == 10

            display_msg = json.loads(display_ws.receive_text())
            assert display_msg["type"] == "display_settings"
            assert display_msg["data"]["verse_limit"] == 10

    def test_all_is_a_valid_verse_limit(self, client):
        """'all' renders the full passage."""
        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            operator_ws.send_text(json.dumps({"action": "set_verse_limit", "verse_limit": "all"}))
            confirmed = json.loads(operator_ws.receive_text())
            assert confirmed["type"] == "display_settings_confirmed"
            assert confirmed["data"]["verse_limit"] == "all"

    def test_invalid_verse_limit_is_rejected(self, client):
        """An unknown limit returns an error and leaves the state unchanged."""
        with client.websocket_connect("/ws/operator") as operator_ws:
            snapshot = json.loads(operator_ws.receive_text())
            assert snapshot["data"]["display_settings"]["verse_limit"] == 3

            operator_ws.send_text(json.dumps({"action": "set_verse_limit", "verse_limit": 7}))
            error = json.loads(operator_ws.receive_text())
            assert error["type"] == "error"
            assert error["data"]["action"] == "set_verse_limit"

            # The connection stays usable after the rejection.
            assert client.get("/api/state").json()["display_settings"]["verse_limit"] == 3

    def test_snapshot_includes_current_settings(self, client):
        """A late-loading operator console renders the value displays are using."""
        with client.websocket_connect("/ws/operator") as operator_ws:
            snapshot = json.loads(operator_ws.receive_text())
            assert snapshot["data"]["display_settings"]["verse_limit"] == 3

            operator_ws.send_text(json.dumps({"action": "set_verse_limit", "verse_limit": 5}))
            assert json.loads(operator_ws.receive_text())["type"] == ("display_settings_confirmed")

        state = client.get("/api/state").json()
        assert state["display_settings"]["verse_limit"] == 5

    def test_operator_set_display_theme_reaches_display(self, client):
        """Choosing light in the console repaints every connected display."""
        with (
            client.websocket_connect("/ws/display") as display_ws,
            client.websocket_connect("/ws/operator") as operator_ws,
        ):
            assert json.loads(display_ws.receive_text())["type"] == "clear"
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

            operator_ws.send_text(json.dumps({"action": "set_display_theme", "theme": "light"}))

            confirmed = json.loads(operator_ws.receive_text())
            assert confirmed["type"] == "display_settings_confirmed"
            assert confirmed["data"]["theme"] == "light"

            display_msg = json.loads(display_ws.receive_text())
            assert display_msg["type"] == "display_settings"
            assert display_msg["data"]["theme"] == "light"

    def test_setting_theme_keeps_verse_limit(self, client):
        """Changing the background does not reset the passage size."""
        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

            operator_ws.send_text(json.dumps({"action": "set_verse_limit", "verse_limit": 10}))
            confirmed = json.loads(operator_ws.receive_text())
            assert confirmed["data"]["verse_limit"] == 10

            operator_ws.send_text(json.dumps({"action": "set_display_theme", "theme": "light"}))
            confirmed = json.loads(operator_ws.receive_text())
            assert confirmed["data"]["verse_limit"] == 10
            assert confirmed["data"]["theme"] == "light"

    def test_invalid_theme_is_rejected(self, client):
        """An unknown theme returns an error and leaves the state dark."""
        with client.websocket_connect("/ws/operator") as operator_ws:
            snapshot = json.loads(operator_ws.receive_text())
            assert snapshot["data"]["display_settings"]["theme"] == "dark"

            operator_ws.send_text(json.dumps({"action": "set_display_theme", "theme": "sepia"}))
            error = json.loads(operator_ws.receive_text())
            assert error["type"] == "error"
            assert error["data"]["action"] == "set_display_theme"

            # The connection stays usable after the rejection.
            assert client.get("/api/state").json()["display_settings"]["theme"] == "dark"

    def test_missing_theme_is_rejected(self, client):
        """A theme action with no value is refused rather than blanking displays."""
        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            operator_ws.send_text(json.dumps({"action": "set_display_theme"}))
            error = json.loads(operator_ws.receive_text())
            assert error["type"] == "error"
            assert error["data"]["action"] == "set_display_theme"
            assert client.get("/api/state").json()["display_settings"]["theme"] == "dark"
