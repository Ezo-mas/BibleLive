"""Manual scripture selection: the passage lookup API and the manual display action.

Covers the two ways an operator names a passage by hand (``GET /api/lookup`` and
the ``display_reference`` WebSocket action) plus the rules that keep manual
selection separate from the speech-to-text workflow: no pending queue mutation,
no confidence, and no way to look like a detection.

The ``client``/``app_state`` fixtures come from tests/conftest.py, which builds
the app over a full KJV database and resets state between tests.
"""

import json

import pytest

from core.config import config
from core.passage import PassageError, resolve_passage
from core.server.models import BibleReference

# NOTE: conftest's autouse ``reset_app_state`` fixture clears pending/history and
# the connection manager before each test, so tests here start from empty state.


@pytest.fixture
def auth_enabled():
    """Enable operator authentication for one test (mirrors test_authentication)."""
    original_require = config.REQUIRE_AUTH
    original_token = config.OPERATOR_TOKEN

    config.REQUIRE_AUTH = True
    config.OPERATOR_TOKEN = "manual-passage-token"

    yield "manual-passage-token"

    config.REQUIRE_AUTH = original_require
    config.OPERATOR_TOKEN = original_token


def _submit_reference(client, book="John", chapter=3, verse_start=16, confidence="high"):
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
    )
    assert response.status_code == 201
    return response.json()["reference_id"]


class TestBooksEndpoint:
    """``GET /api/books`` serves the shared canon rather than a frontend copy."""

    def test_returns_the_whole_canon_in_order(self, client):
        from core.books import CANONICAL_BOOKS

        response = client.get("/api/books")
        assert response.status_code == 200
        books = response.json()["books"]

        assert [book["name"] for book in books] == [book.name for book in CANONICAL_BOOKS]
        assert [book["order"] for book in books] == list(range(1, 67))
        # Translated names are an addition (see test_book_names.py); the canonical
        # fields below are what every request names a book by.
        canonical = [
            {
                key: value
                for key, value in book.items()
                if key not in ("local_name", "local_variants")
            }
            for book in books
        ]
        assert canonical[0] == {
            "name": "Genesis",
            "chapters": 50,
            "order": 1,
            "abbrev": "gn",
            "variants": ["genesis"],
        }
        assert canonical[-1] == {
            "name": "Revelation",
            "chapters": 22,
            "order": 66,
            "abbrev": "re",
            "variants": ["revelation", "revelations", "revelation of john"],
        }

    def test_abbreviations_and_variants_come_from_the_canon(self, client):
        """The search box's name matching is built from the canon, not a copy."""
        from core.books import CANONICAL_BOOKS

        books = client.get("/api/books").json()["books"]
        served = {book["name"]: (book["abbrev"], tuple(book["variants"])) for book in books}
        assert served == {book.name: (book.abbrev, book.variants) for book in CANONICAL_BOOKS}
        assert served["Psalms"][0] == "ps"
        assert "psalm" in served["Psalms"][1]

    def test_chapter_counts_match_the_canon(self, client):
        from core.books import CANONICAL_BOOKS

        books = client.get("/api/books").json()["books"]
        chapters = {book["name"]: book["chapters"] for book in books}
        assert chapters["Psalms"] == 150
        assert chapters == {book.name: book.chapters for book in CANONICAL_BOOKS}

    def test_requires_authentication_when_enabled(self, client, auth_enabled):
        assert client.get("/api/books").status_code == 401
        assert client.get("/api/books", headers={"Authorization": "Bearer nope"}).status_code == 401

        token = auth_enabled
        response = client.get("/api/books", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert len(response.json()["books"]) == 66


class TestPassageLookup:
    """``GET /api/lookup`` resolves a passage to the text the display would show."""

    def test_single_verse(self, client):
        response = client.get("/api/lookup?book=John&chapter=3&verse_start=16")
        assert response.status_code == 200
        data = response.json()

        assert data["book"] == "John"
        assert data["chapter"] == 3
        assert data["verse_start"] == 16
        assert data["verse_end"] == 16
        assert [verse["verse"] for verse in data["verses"]] == [16]
        assert data["text"].startswith("For God so loved the world")
        assert data["text"] == " ".join(verse["text"] for verse in data["verses"])

    def test_range_returns_every_verse(self, client):
        data = client.get("/api/lookup?book=Genesis&chapter=1&verse_start=1&verse_end=3").json()

        assert data["verse_start"] == 1
        assert data["verse_end"] == 3
        assert [verse["verse"] for verse in data["verses"]] == [1, 2, 3]
        assert data["verses"][0]["text"] == "In the beginning God created the heaven and the earth."

    def test_chapter_only_lookup_starts_at_verse_one(self, client):
        data = client.get("/api/lookup?book=Luke&chapter=1").json()

        assert data["verse_start"] == 1
        assert data["verses"][0]["verse"] == 1
        assert data["text"].startswith("Forasmuch as many have taken in hand")

    def test_book_name_is_case_insensitive(self, client):
        data = client.get("/api/lookup?book=john&chapter=3&verse_start=16").json()
        assert data["book"] == "John"

    def test_text_is_cleaned_of_editorial_markup(self, client):
        data = client.get("/api/lookup?book=Genesis&chapter=1&verse_start=2").json()
        assert "{" not in data["text"] and "[" not in data["text"]

    def test_unknown_book_is_not_found(self, client):
        response = client.get("/api/lookup?book=Hezekiah&chapter=1")
        assert response.status_code == 404
        assert "Unknown book" in response.json()["detail"]

    def test_chapter_beyond_the_book_is_rejected(self, client):
        response = client.get("/api/lookup?book=Genesis&chapter=51")
        assert response.status_code == 422
        assert "50" in response.json()["detail"]

    def test_verse_missing_from_the_database_is_not_found(self, client):
        response = client.get("/api/lookup?book=Genesis&chapter=1&verse_start=999")
        assert response.status_code == 404
        assert "Genesis 1:999" in response.json()["detail"]

    def test_end_verse_missing_from_the_database_is_not_found(self, client):
        """Both bounds must exist, so a range cannot silently show less than asked.

        The range is short enough to pass the length check, so this is the verse
        lookup that rejects it rather than the limit.
        """
        response = client.get("/api/lookup?book=Genesis&chapter=1&verse_start=30&verse_end=60")
        assert response.status_code == 404
        assert "Genesis 1:60" in response.json()["detail"]

    def test_reversed_range_is_rejected(self, client):
        response = client.get("/api/lookup?book=John&chapter=3&verse_start=18&verse_end=16")
        assert response.status_code == 422
        assert "start at or before" in response.json()["detail"]

    def test_range_longer_than_the_limit_is_rejected(self, client):
        over = config.MAX_VERSES_PER_REFERENCE + 1
        response = client.get(f"/api/lookup?book=Psalms&chapter=119&verse_start=1&verse_end={over}")
        assert response.status_code == 422
        assert str(config.MAX_VERSES_PER_REFERENCE) in response.json()["detail"]

    def test_range_at_the_limit_is_accepted(self, client):
        limit = config.MAX_VERSES_PER_REFERENCE
        data = client.get(
            f"/api/lookup?book=Psalms&chapter=119&verse_start=1&verse_end={limit}"
        ).json()
        assert len(data["verses"]) == limit

    @pytest.mark.parametrize(
        "query",
        [
            "book=John",  # chapter missing
            "chapter=3",  # book missing
            "book=John&chapter=0",
            "book=John&chapter=3&verse_start=0",
            "book=John&chapter=3&verse_start=notanumber",
            "",  # nothing at all
        ],
    )
    def test_malformed_parameters_are_rejected(self, client, query):
        assert client.get(f"/api/lookup?{query}").status_code == 422

    def test_requires_authentication_when_enabled(self, client, auth_enabled):
        assert client.get("/api/lookup?book=John&chapter=3&verse_start=16").status_code == 401

        token = auth_enabled
        response = client.get(
            "/api/lookup?book=John&chapter=3&verse_start=16",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200


class TestResolvePassage:
    """The shared resolver's own guard rails (used by both callers)."""

    def test_requires_loaded_bible_text(self):
        class _UnloadedLookup:
            def is_loaded(self):
                return False

        with pytest.raises(PassageError) as error:
            resolve_passage(_UnloadedLookup(), book="John", chapter=3, verse_start=16)

        assert error.value.status_code == 503

    @pytest.mark.parametrize(
        ("book", "chapter", "verse_start", "verse_end"),
        [
            (None, 3, 16, None),
            ("", 3, 16, None),
            ("John", "3", 16, None),
            ("John", True, 16, None),
            ("John", 3, "sixteen", None),
            ("John", 3, 16, 0),
        ],
    )
    def test_non_numeric_coordinates_are_rejected(self, book, chapter, verse_start, verse_end):
        """The WebSocket handler reads raw JSON, so types are checked here too."""

        class _Lookup:
            def is_loaded(self):
                return True

        with pytest.raises(PassageError) as error:
            resolve_passage(
                _Lookup(),
                book=book,
                chapter=chapter,
                verse_start=verse_start,
                verse_end=verse_end,
            )
        assert error.value.status_code in (404, 422)


class TestManualDisplayAction:
    """``display_reference`` shows an operator-chosen passage without touching PENDING."""

    def test_displays_the_passage_on_connected_displays(self, client, app_state):
        with (
            client.websocket_connect("/ws/display") as display_ws,
            client.websocket_connect("/ws/operator") as operator_ws,
        ):
            assert json.loads(display_ws.receive_text())["type"] == "clear"
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

            operator_ws.send_text(
                json.dumps(
                    {
                        "action": "display_reference",
                        "book": "John",
                        "chapter": 3,
                        "verse_start": 16,
                        "verse_end": 17,
                    }
                )
            )

            confirmed = json.loads(operator_ws.receive_text())
            assert confirmed["type"] == "display_confirmed"
            reference = confirmed["data"]["reference"]
            assert reference["book"] == "John"
            assert reference["chapter"] == 3
            assert reference["verse_start"] == 16
            assert reference["verse_end"] == 17
            assert [verse["verse"] for verse in reference["verses"]] == [16, 17]
            # A manual passage is labelled as manual and carries no confidence, so
            # nothing downstream can read it as a detection.
            assert reference["source"] == "manual"
            assert reference["confidence"] is None

            display_msg = json.loads(display_ws.receive_text())
            assert display_msg["type"] == "reference"
            assert display_msg["data"]["book"] == "John"
            assert display_msg["data"]["verses"][0]["text"].startswith("For God so loved")

        assert app_state.current_display is not None
        assert app_state.current_display.source == "manual"
        assert app_state.current_display.confidence is None

    def test_does_not_add_anything_to_pending(self, client, app_state):
        first = _submit_reference(client, book="Psalms", chapter=23, verse_start=1)
        second = _submit_reference(client, book="Romans", chapter=8, verse_start=28)

        with client.websocket_connect("/ws/operator") as operator_ws:
            snapshot = json.loads(operator_ws.receive_text())
            assert len(snapshot["data"]["pending"]) == 2

            operator_ws.send_text(
                json.dumps(
                    {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 16}
                )
            )
            assert json.loads(operator_ws.receive_text())["type"] == "display_confirmed"

            # No pending_update follows: the queue is untouched. Prove it by asking
            # for the next message to be the next action's confirmation.
            operator_ws.send_text(json.dumps({"action": "clear"}))
            assert json.loads(operator_ws.receive_text())["type"] == "clear_confirmed"

        assert [ref.id for ref in app_state.pending_references] == [first, second]
        state = client.get("/api/state").json()
        assert state["pending_count"] == 2
        assert all(ref["source"] == "detected" for ref in state["pending"])

    def test_manual_passage_never_enters_pending_or_the_approval_flow(self, client, app_state):
        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            operator_ws.send_text(
                json.dumps(
                    {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 16}
                )
            )
            confirmed = json.loads(operator_ws.receive_text())

        manual_id = confirmed["data"]["reference"]["id"]
        assert app_state.pending_references == []

        # The manual reference is not approvable: approval only ever resolves an ID
        # in the pending queue, and this one was never queued.
        response = client.post("/api/approve", json={"reference_id": manual_id})
        assert response.status_code == 404

    def test_manual_display_does_not_disturb_the_pending_queue_or_auto_approve(
        self, client, app_state
    ):
        """Switching auto-approve on must not change how manual selection behaves."""
        pending_id = _submit_reference(client, book="John", chapter=3, verse_start=16)

        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            operator_ws.send_text(json.dumps({"action": "set_auto_mode", "auto_mode": "all"}))
            assert json.loads(operator_ws.receive_text())["type"] == "auto_mode_confirmed"

            operator_ws.send_text(
                json.dumps(
                    {
                        "action": "display_reference",
                        "book": "Psalms",
                        "chapter": 23,
                        "verse_start": 1,
                        "verse_end": 2,
                    }
                )
            )
            confirmed = json.loads(operator_ws.receive_text())
            assert confirmed["type"] == "display_confirmed"
            assert confirmed["data"]["reference"]["source"] == "manual"

        # The queued detection is still waiting for the operator.
        assert [ref.id for ref in app_state.pending_references] == [pending_id]
        assert client.get("/api/state").json()["pending_count"] == 1

    def test_records_the_passage_in_history_like_an_approval(self, client, app_state):
        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            operator_ws.send_text(
                json.dumps(
                    {
                        "action": "display_reference",
                        "book": "Genesis",
                        "chapter": 1,
                        "verse_start": 1,
                    }
                )
            )
            assert json.loads(operator_ws.receive_text())["type"] == "display_confirmed"

        state = client.get("/api/state").json()
        assert state["current_display"]["book"] == "Genesis"
        assert state["history_count"] == 1
        assert state["history"][0]["source"] == "manual"

    def test_unknown_book_returns_a_readable_error_and_keeps_the_connection(
        self, client, app_state
    ):
        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

            operator_ws.send_text(
                json.dumps(
                    {
                        "action": "display_reference",
                        "book": "Genesisx",
                        "chapter": 1,
                        "verse_start": 1,
                    }
                )
            )
            error = json.loads(operator_ws.receive_text())
            assert error["type"] == "error"
            assert error["data"]["action"] == "display_reference"
            assert "Unknown book" in error["data"]["message"]

            # A rejected request changes nothing, and the socket still works.
            assert app_state.current_display is None
            operator_ws.send_text(
                json.dumps(
                    {
                        "action": "display_reference",
                        "book": "Genesis",
                        "chapter": 1,
                        "verse_start": 1,
                    }
                )
            )
            assert json.loads(operator_ws.receive_text())["type"] == "display_confirmed"

    def test_missing_verse_returns_an_error_and_leaves_the_display_alone(self, client, app_state):
        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            operator_ws.send_text(
                json.dumps(
                    {
                        "action": "display_reference",
                        "book": "Genesis",
                        "chapter": 1,
                        "verse_start": 999,
                    }
                )
            )
            error = json.loads(operator_ws.receive_text())
            assert error["type"] == "error"
            assert "Genesis 1:999" in error["data"]["message"]
            assert app_state.current_display is None

    def test_range_beyond_the_limit_is_rejected_over_the_socket(self, client, app_state):
        over = config.MAX_VERSES_PER_REFERENCE + 1
        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            operator_ws.send_text(
                json.dumps(
                    {
                        "action": "display_reference",
                        "book": "Psalms",
                        "chapter": 119,
                        "verse_start": 1,
                        "verse_end": over,
                    }
                )
            )
            error = json.loads(operator_ws.receive_text())
            assert error["type"] == "error"
            assert str(config.MAX_VERSES_PER_REFERENCE) in error["data"]["message"]
            assert app_state.current_display is None

    @pytest.mark.parametrize(
        "payload",
        [
            {"action": "display_reference"},  # no coordinates
            {"action": "display_reference", "book": "John"},  # chapter missing
            {"action": "display_reference", "book": "John", "chapter": "three"},  # wrong type
            {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 0},
            {"action": "display_reference", "book": "x" * 101, "chapter": 3},  # oversized
        ],
    )
    def test_malformed_payloads_are_rejected(self, client, app_state, payload):
        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            operator_ws.send_text(json.dumps(payload))

            error = json.loads(operator_ws.receive_text())
            assert error["type"] == "error"
            assert error["data"]["action"] == "display_reference"
            assert app_state.current_display is None

    def test_operator_can_clear_a_manually_displayed_passage(self, client, app_state):
        """Manual display is an ordinary display update, so Clear still applies."""
        with client.websocket_connect("/ws/operator") as operator_ws:
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            operator_ws.send_text(
                json.dumps(
                    {
                        "action": "display_reference",
                        "book": "Genesis",
                        "chapter": 1,
                        "verse_start": 1,
                    }
                )
            )
            assert json.loads(operator_ws.receive_text())["type"] == "display_confirmed"

            operator_ws.send_text(json.dumps({"action": "clear"}))
            assert json.loads(operator_ws.receive_text())["type"] == "clear_confirmed"

        assert app_state.current_display is None

    def test_manual_display_keeps_the_single_display_send_path(self, client, app_state):
        """The passage reaches displays as a ``reference`` message, not a new shape."""
        with (
            client.websocket_connect("/ws/display") as display_ws,
            client.websocket_connect("/ws/operator") as operator_ws,
        ):
            assert json.loads(display_ws.receive_text())["type"] == "clear"
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

            operator_ws.send_text(
                json.dumps(
                    {"action": "display_reference", "book": "Luke", "chapter": 2, "verse_start": 11}
                )
            )
            assert json.loads(operator_ws.receive_text())["type"] == "display_confirmed"

            message = json.loads(display_ws.receive_text())
            assert message["type"] == "reference"
            assert set(message["data"]) >= {"id", "book", "chapter", "verses", "text"}


class TestManualReferenceModel:
    """The reference shape itself: manual passages cannot claim a detection's fields."""

    def test_manual_reference_has_no_confidence_and_is_marked_manual(self):
        ref = BibleReference(book="John", chapter=3, verse_start=16, source="manual")
        assert ref.confidence is None
        assert ref.source == "manual"

    def test_detected_references_default_to_detected(self):
        ref = BibleReference(book="John", chapter=3, verse_start=16, confidence="high")
        assert ref.source == "detected"

    def test_should_auto_approve_never_matches_a_manual_reference(self, app_state):
        ref = BibleReference(book="John", chapter=3, verse_start=16, source="manual")
        for mode in ("off", "high", "medium_and_up", "all"):
            assert app_state.should_auto_approve(ref, mode) is False
