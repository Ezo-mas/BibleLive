"""History and Show Again: what the console lists, and showing an entry again.

The operator console lists the passages that already reached the display
(``AppState.reference_history``) and lets the operator stage one in Up Next and
show it again. There is no new server action and nothing is persisted: "show
again" is the coordinate-only ``display_reference`` request the manual search
already sends, re-resolved by ``core/passage.py`` against the canon and the
server's own Bible text, and sent through ``_send_display_reference`` — the one
path that puts a reference on a projector.

These tests pin the model the console relies on (newest first, bounded, stable
IDs, a twenty-entry snapshot window) and the deliberate history behaviour of the
action it reuses: every display, including a repeat, is a new entry.

The ``client``/``app_state`` fixtures come from tests/conftest.py, which builds the
app over a full KJV database and resets state between tests.
"""

import json

import pytest

from core.config import config

# NOTE: conftest's autouse ``reset_app_state`` fixture clears pending/history and
# the connection manager before each test, so tests here start from empty state.


@pytest.fixture
def auth_enabled():
    """Enable operator authentication for one test (mirrors test_manual_reference)."""
    original_require = config.REQUIRE_AUTH
    original_token = config.OPERATOR_TOKEN

    config.REQUIRE_AUTH = True
    config.OPERATOR_TOKEN = "history-replay-token"

    yield "history-replay-token"

    config.REQUIRE_AUTH = original_require
    config.OPERATOR_TOKEN = original_token


def _submit_reference(
    client, book="John", chapter=3, verse_start=16, confidence="high", headers=None
):
    """Queue a detection-like reference and return its ID."""
    response = client.post(
        "/api/reference",
        json={
            "book": book,
            "chapter": chapter,
            "verse_start": verse_start,
            "confidence": confidence,
            "inferred": False,
            "text": "",
        },
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()["reference_id"]


def _approve(client, reference_id, headers=None):
    """Approve a queued reference the way an operator does, and return its ID."""
    response = client.post("/api/approve", json={"reference_id": reference_id}, headers=headers)
    assert response.status_code == 200
    return response.json()["reference"]["id"]


def _display(client, book="John", chapter=3, verse_start=16, headers=None):
    """Queue, approve, and return the history entry that resulted."""
    _approve(
        client,
        _submit_reference(
            client, book=book, chapter=chapter, verse_start=verse_start, headers=headers
        ),
        headers=headers,
    )
    return client.get("/api/state", headers=headers).json()["history"][0]


def _replay(operator_ws, entry, **extra):
    """Send the coordinates of a history entry the way the console's Show does."""
    payload = {
        "action": "display_reference",
        "book": entry["book"],
        "chapter": entry["chapter"],
        "verse_start": entry["verse_start"],
        "verse_end": entry["verse_end"],
    }
    payload.update(extra)
    operator_ws.send_text(json.dumps(payload))


class TestHistoryModel:
    """The list the console renders: newest first, bounded, with stable identities."""

    def test_only_approved_references_enter_history(self, client, app_state):
        pending_id = _submit_reference(client)
        assert client.get("/api/state").json()["history_count"] == 0

        _approve(client, pending_id)

        state = client.get("/api/state").json()
        assert state["history_count"] == 1
        assert state["history"][0]["id"] == pending_id
        assert state["history"][0]["source"] == "detected"
        assert state["history"][0]["confidence"] == "high"

    def test_newest_first_and_the_newest_entry_is_what_is_on_display(self, client, app_state):
        first = _approve(client, _submit_reference(client, book="John", chapter=3, verse_start=16))
        second = _approve(
            client, _submit_reference(client, book="Romans", chapter=8, verse_start=28)
        )

        history = client.get("/api/state").json()["history"]
        assert [entry["id"] for entry in history] == [second, first]
        assert app_state.current_display is not None
        assert app_state.current_display.id == second

    def test_every_entry_has_a_stable_unique_id(self, client):
        approved = [
            _approve(client, _submit_reference(client, verse_start=verse)) for verse in (16, 17, 18)
        ]

        history = client.get("/api/state").json()["history"]
        ids = [entry["id"] for entry in history]
        assert ids == list(reversed(approved))
        assert len(set(ids)) == 3
        # Newest first is the only reordering: reading the state twice is stable.
        assert client.get("/api/state").json()["history"] == history

    def test_the_same_passage_shown_twice_keeps_two_distinct_entries(self, client):
        """History is a log of what was displayed, so a repeat is its own entry."""
        first = _approve(client, _submit_reference(client, verse_start=16))
        second = _approve(client, _submit_reference(client, verse_start=16))

        history = client.get("/api/state").json()["history"]
        assert [entry["id"] for entry in history] == [second, first]
        assert [entry["verse_start"] for entry in history] == [16, 16]

    def test_history_is_bounded_to_the_newest_entries(self, client, monkeypatch):
        monkeypatch.setattr(config, "MAX_HISTORY_REFERENCES", 3)
        approved = [
            _approve(client, _submit_reference(client, verse_start=verse))
            for verse in (16, 17, 18, 19)
        ]

        state = client.get("/api/state").json()
        assert [entry["id"] for entry in state["history"]] == list(reversed(approved))[:3]
        assert state["history_count"] == 3

    def test_snapshot_window_is_twenty_while_the_count_is_the_whole_history(self, client):
        for verse in range(1, 26):
            _approve(client, _submit_reference(client, verse_start=verse))

        state = client.get("/api/state").json()
        assert state["history_count"] == 25
        assert len(state["history"]) == 20
        # The window is the newest twenty, newest first.
        assert [entry["verse_start"] for entry in state["history"]] == list(range(25, 5, -1))

    def test_operator_snapshot_carries_the_same_history_newest_first(self, client):
        approved = [
            _approve(client, _submit_reference(client, book=book, chapter=chapter, verse_start=1))
            for book, chapter in (("John", 3), ("Romans", 8))
        ]

        with client.websocket_connect("/ws/operator") as operator_ws:
            snapshot = json.loads(operator_ws.receive_text())

        assert snapshot["type"] == "state_snapshot"
        assert [entry["id"] for entry in snapshot["data"]["history"]] == list(reversed(approved))
        assert snapshot["data"]["history_count"] == 2
        assert snapshot["data"]["current_display"]["id"] == approved[1]


class TestShowAgain:
    """Replaying a history entry through the existing ``display_reference`` action."""

    def test_replaying_an_entry_displays_that_passage_from_the_database(self, client, app_state):
        entry = _display(client, book="John", chapter=3, verse_start=16)
        assert entry["source"] == "detected"

        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            _replay(operator_ws, entry)
            confirmed = json.loads(operator_ws.receive_text())

        assert confirmed["type"] == "display_confirmed"
        replayed = confirmed["data"]["reference"]
        assert (replayed["book"], replayed["chapter"], replayed["verse_start"]) == ("John", 3, 16)
        assert replayed["text"].startswith("For God so loved the world")
        assert app_state.current_display.id == replayed["id"]

    def test_replay_appends_one_entry_and_leaves_the_original_untouched(self, client, app_state):
        original_id = _approve(client, _submit_reference(client))
        before = client.get("/api/state").json()

        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            _replay(operator_ws, before["history"][0])
            confirmed = json.loads(operator_ws.receive_text())
        replayed = confirmed["data"]["reference"]

        after = client.get("/api/state").json()
        # One display, one new entry: nothing recursive, nothing mutated.
        assert after["history_count"] == before["history_count"] + 1
        assert after["history"][0]["id"] == replayed["id"] != original_id
        assert after["history"][1] == before["history"][0]
        assert app_state.reference_history[1].id == original_id

        # A repeat display is the operator showing a passage by hand: it is
        # labelled manual and carries no confidence, so it cannot be read as a
        # detection or auto-approved.
        assert replayed["source"] == "manual"
        assert replayed["confidence"] is None

    def test_replay_is_bounded_by_the_same_history_limit(self, client, monkeypatch):
        monkeypatch.setattr(config, "MAX_HISTORY_REFERENCES", 3)
        entry = _display(client, book="Romans", chapter=8, verse_start=28)

        for _ in range(3):
            with client.websocket_connect("/ws/operator") as operator_ws:
                assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
                _replay(operator_ws, entry)
                assert json.loads(operator_ws.receive_text())["type"] == "display_confirmed"

        state = client.get("/api/state").json()
        assert state["history_count"] == 3
        assert entry["id"] not in [item["id"] for item in state["history"]]

    def test_replay_ignores_client_supplied_text_id_and_confidence(self, client, app_state):
        """Coordinates are all that is trusted; everything else is re-resolved."""
        entry = _display(client, book="Genesis", chapter=1, verse_start=1)

        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            _replay(
                operator_ws,
                entry,
                id="forged-id",
                text="Forged verse text.",
                confidence="high",
                source="detected",
                verses=[{"verse": 1, "text": "Forged verse text."}],
            )
            confirmed = json.loads(operator_ws.receive_text())

        replayed = confirmed["data"]["reference"]
        assert replayed["id"] != "forged-id"
        assert replayed["text"].startswith("In the beginning God created")
        assert "Forged verse text." not in replayed["text"]
        assert replayed["source"] == "manual"
        assert replayed["confidence"] is None
        assert app_state.current_display is not None
        assert app_state.current_display.text.startswith("In the beginning God created")

    def test_an_entry_evicted_from_history_can_still_be_shown_again(
        self, client, app_state, monkeypatch
    ):
        """The console may hold a passage whose entry has aged out of the window.

        Showing it again does not depend on the entry existing: the request is
        coordinates, and the canon and Bible database are the authority.
        """
        monkeypatch.setattr(config, "MAX_HISTORY_REFERENCES", 2)
        evicted = _display(client, book="Psalms", chapter=23, verse_start=1)
        _display(client, book="Romans", chapter=8, verse_start=28)
        _display(client, book="Luke", chapter=2, verse_start=11)

        assert evicted["id"] not in [
            entry["id"] for entry in client.get("/api/state").json()["history"]
        ]

        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            _replay(operator_ws, evicted)
            confirmed = json.loads(operator_ws.receive_text())

        assert confirmed["type"] == "display_confirmed"
        replayed = confirmed["data"]["reference"]
        assert (replayed["book"], replayed["chapter"], replayed["verse_start"]) == ("Psalms", 23, 1)
        assert replayed["verses"][0]["verse"] == 1
        assert app_state.current_display.id == replayed["id"] != evicted["id"]

    def test_an_unavailable_passage_fails_without_changing_the_display(
        self, client, app_state, monkeypatch
    ):
        approved = _approve(client, _submit_reference(client))
        entry = client.get("/api/state").json()["history"][0]

        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

            # The text this entry points at is gone (database reloaded, removed):
            # the request fails, the display keeps what it had, and history does
            # not record a display that never happened.
            monkeypatch.setattr(app_state.bible_lookup, "is_loaded", lambda: False)
            _replay(operator_ws, entry)
            error = json.loads(operator_ws.receive_text())

            assert error["type"] == "error"
            assert error["data"]["action"] == "display_reference"
            assert "not available" in error["data"]["message"]
            assert app_state.current_display is not None
            assert app_state.current_display.id == approved
            assert client.get("/api/state").json()["history_count"] == 1

            # The socket is still usable once the text is back.
            monkeypatch.undo()
            _replay(operator_ws, entry)
            assert json.loads(operator_ws.receive_text())["type"] == "display_confirmed"

    def test_replay_does_not_touch_the_pending_queue(self, client, app_state):
        pending = [
            _submit_reference(client, book="John", chapter=3, verse_start=16),
            _submit_reference(client, book="Romans", chapter=8, verse_start=28),
        ]
        _approve(client, pending[0])
        entry = client.get("/api/state").json()["history"][0]

        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            _replay(operator_ws, entry)
            assert json.loads(operator_ws.receive_text())["type"] == "display_confirmed"
            # No pending_update follows: prove it by asking for the next message
            # to be the next action's confirmation.
            operator_ws.send_text(json.dumps({"action": "clear"}))
            assert json.loads(operator_ws.receive_text())["type"] == "clear_confirmed"

        assert [ref.id for ref in app_state.pending_references] == [pending[1]]
        assert client.get("/api/state").json()["pending_count"] == 1

    def test_replay_reaches_displays_through_the_single_send_path(
        self, client, app_state, monkeypatch
    ):
        entry = _display(client, book="Luke", chapter=2, verse_start=11)

        sends = []
        original = app_state._send_display_reference

        async def spy(ref):
            sends.append(ref)
            await original(ref)

        monkeypatch.setattr(app_state, "_send_display_reference", spy)

        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            _replay(operator_ws, entry)
            confirmed = json.loads(operator_ws.receive_text())

        assert [ref.id for ref in sends] == [confirmed["data"]["reference"]["id"]]
        assert len(sends) == 1

    def test_a_display_client_receives_the_replayed_passage(self, client, app_state):
        entry = _display(client, book="John", chapter=3, verse_start=16)

        with (
            client.websocket_connect("/ws/display") as display_ws,
            client.websocket_connect("/ws/operator") as operator_ws,
        ):
            assert json.loads(display_ws.receive_text())["type"] == "reference"  # the first display
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

            _replay(operator_ws, entry)
            assert json.loads(operator_ws.receive_text())["type"] == "display_confirmed"

            message = json.loads(display_ws.receive_text())

        assert message["type"] == "reference"
        assert message["data"]["book"] == "John"
        assert message["data"]["verse_start"] == 16

    def test_replay_requires_an_authenticated_operator_socket(self, client, auth_enabled):
        """Show Again rides the authenticated operator socket and nothing else.

        That an unauthenticated operator socket is rejected at all is covered by
        tests/test_authentication.py; this is the Phase 4 flow end to end.
        """
        token = auth_enabled
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/state").status_code == 401
        entry = _display(client, book="Romans", chapter=8, verse_start=28, headers=headers)

        with client.websocket_connect(f"/ws/operator?token={token}") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            _replay(operator_ws, entry)

            confirmed = json.loads(operator_ws.receive_text())

        assert confirmed["type"] == "display_confirmed"
        assert confirmed["data"]["reference"]["book"] == "Romans"
