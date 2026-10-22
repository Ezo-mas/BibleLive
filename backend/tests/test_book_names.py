"""Book names follow the loaded Bible translation, for display and search only.

The names come from the ``local_name`` column of the translation the server has
loaded (``ltb``); a translation without them (``kjv``) keeps the English names. The
canon, the database, and every request keep using the English name, so these tests
pin that the translated label is an addition on top and never replaces it.
"""

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.book_names import BookNames, lithuanian_search_variants, read_local_names
from core.config import config
from core.server.app import create_app
from core.server.follow_along import FollowAlong
from core.server.models import BibleReference, FollowAlongHint

# Real names from the ltb database, for the books these tests use.
LTB_NAMES = {
    "Genesis": "Pradžios",
    "Jonah": "Jonos",
    "John": "Jono evangelija",
    "1 John": "1 Jono",
    "Psalms": "Psalmynas",
    "1 Corinthians": "1 Korintiečiams",
}


def make_bible(path: Path, translation: str, text: str, names: dict[str, str] | None) -> None:
    """A minimal Bible database in the real schema, with John 3:16 and Psalm 23:1.

    ``names`` None means no ``local_name`` column at all, like the KJV import.
    """
    conn = sqlite3.connect(path)
    local = ", local_name TEXT" if names is not None else ""
    conn.executescript(
        f"""
        CREATE TABLE books (id INTEGER PRIMARY KEY, name TEXT, testament TEXT,
                            book_order INTEGER{local});
        CREATE TABLE verses (id INTEGER PRIMARY KEY, book_id INTEGER, chapter INTEGER,
                             verse INTEGER, text TEXT);
        CREATE TABLE bible_metadata (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO bible_metadata VALUES ('translation', '{translation}');
        """
    )
    for book_id, (name, order) in enumerate([("John", 43), ("Psalms", 19), ("Mark", 41)], start=1):
        if names is None:
            conn.execute(
                "INSERT INTO books (id, name, testament, book_order) VALUES (?, ?, 'X', ?)",
                (book_id, name, order),
            )
        else:
            conn.execute(
                "INSERT INTO books (id, name, testament, book_order, local_name) "
                "VALUES (?, ?, 'X', ?, ?)",
                (book_id, name, order, names.get(name)),
            )
    conn.executemany(
        "INSERT INTO verses (book_id, chapter, verse, text) VALUES (?, ?, ?, ?)",
        [(1, 3, 16, text), (1, 3, 17, text + " 17"), (2, 23, 1, text), (3, 1, 1, text)],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def bibles(tmp_path: Path):
    """A data directory with an English KJV and a Lithuanian LTB that names its books."""
    data = tmp_path / "bible_data"
    data.mkdir()
    make_bible(data / "biblelive.sqlite", "kjv", "For God so loved the world", None)
    make_bible(data / "bible_ltb.sqlite", "ltb", "Nes Dievas taip pamilo pasauli", LTB_NAMES)

    original = (config.DB_PATH, config.SETTINGS_PATH)
    config.DB_PATH = str(data / "biblelive.sqlite")
    config.SETTINGS_PATH = str(tmp_path / "settings.json")
    yield data
    config.DB_PATH, config.SETTINGS_PATH = original


@pytest.fixture
def client(bibles: Path):
    with TestClient(create_app()) as test_client:
        yield test_client


def use_version(client: TestClient, version: str) -> None:
    response = client.post("/api/bible-version", json={"version": version})
    assert response.status_code == 200, response.text


def detect(client: TestClient, book: str = "John", chapter: int = 3, verse: int = 16) -> str:
    response = client.post(
        "/api/reference",
        json={"book": book, "chapter": chapter, "verse_start": verse, "confidence": "high"},
    )
    assert response.status_code == 201, response.text
    return response.json()["reference_id"]


def pending(client: TestClient) -> list[dict]:
    return client.get("/api/state").json()["pending"]


class TestReadLocalNames:
    def test_reads_the_translations_own_names(self, bibles):
        assert read_local_names(bibles / "bible_ltb.sqlite") == {
            name: label for name, label in LTB_NAMES.items() if name in ("John", "Psalms")
        }

    def test_a_database_without_the_column_has_no_names(self, bibles):
        assert read_local_names(bibles / "biblelive.sqlite") == {}

    def test_blank_and_non_canonical_rows_are_skipped(self, tmp_path):
        path = tmp_path / "odd.sqlite"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE books (name TEXT, local_name TEXT)")
        conn.executemany(
            "INSERT INTO books VALUES (?, ?)",
            [("John", "  "), ("Genesis", " Pradžios "), ("Not A Book", "Klaida"), ("Mark", None)],
        )
        conn.commit()
        conn.close()
        assert read_local_names(path) == {"Genesis": "Pradžios"}

    def test_an_unreadable_file_has_no_names(self, tmp_path):
        broken = tmp_path / "broken.sqlite"
        broken.write_bytes(b"not a database")
        assert read_local_names(broken) == {}
        assert read_local_names(tmp_path / "missing.sqlite") == {}


class TestBookNames:
    def test_label_comes_from_the_database_it_is_asked_about(self, bibles):
        names = BookNames()
        assert names.label("John", bibles / "bible_ltb.sqlite") == "Jono evangelija"
        assert names.label("John", bibles / "biblelive.sqlite") is None
        assert names.label("Mark", bibles / "bible_ltb.sqlite") is None  # no name for it
        assert names.label("John", bibles / "missing.sqlite") is None

    def test_a_reimported_database_is_picked_up_without_a_restart(self, bibles):
        names = BookNames()
        path = bibles / "bible_ltb.sqlite"
        assert names.label("John", path) == "Jono evangelija"

        conn = sqlite3.connect(path)
        conn.execute("UPDATE books SET local_name = 'Jono' WHERE name = 'John'")
        conn.commit()
        conn.close()
        # Same size, new write time: the cache key is the file's modification time.
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        assert names.label("John", path) == "Jono"

    def test_search_spellings_cover_numbered_books(self):
        variants = lithuanian_search_variants()
        assert "1 korintiec" in variants["1 Corinthians"]
        assert "2 korintiec" in variants["2 Corinthians"]
        assert "pradz" in variants["Genesis"]
        assert "jonas" in variants["John"]


class TestBooksEndpoint:
    def test_english_translation_has_no_local_names(self, client):
        books = client.get("/api/books").json()["books"]
        assert all(book["local_name"] is None and book["local_variants"] == [] for book in books)

    def test_lithuanian_translation_names_its_books(self, client):
        use_version(client, "ltb")
        books = {book["name"]: book for book in client.get("/api/books").json()["books"]}
        assert books["John"]["local_name"] == "Jono evangelija"
        assert "jonas" in books["John"]["local_variants"]
        # The canonical fields are untouched, and a book the database has no name for
        # simply has none.
        assert books["John"]["abbrev"] == "jn" and books["John"]["variants"] == ["john"]
        assert books["Mark"]["local_name"] is None

    def test_switching_back_drops_the_names(self, client):
        use_version(client, "ltb")
        use_version(client, "kjv")
        books = client.get("/api/books").json()["books"]
        assert all(book["local_name"] is None for book in books)


class TestReferenceLabels:
    def test_english_has_no_label(self, client):
        detect(client)
        assert pending(client)[0]["book"] == "John"
        assert pending(client)[0]["book_label"] is None

    def test_a_detection_is_labelled_by_the_loaded_translation(self, client):
        use_version(client, "ltb")
        detect(client)
        assert pending(client)[0]["book"] == "John"
        assert pending(client)[0]["book_label"] == "Jono evangelija"

    def test_the_label_belongs_to_the_text_on_screen(self, client):
        use_version(client, "ltb")
        reference = client.post("/api/approve", json={"reference_id": detect(client)}).json()[
            "reference"
        ]
        assert reference["book_label"] == "Jono evangelija"
        assert "Dievas" in reference["text"]  # Lithuanian name over Lithuanian text

        # The operator picks KJV by hand, whatever language is spoken: the next
        # detection reads in English, name and text alike.
        use_version(client, "kjv")
        reference = client.post("/api/approve", json={"reference_id": detect(client)}).json()[
            "reference"
        ]
        assert reference["book_label"] is None
        assert "loved the world" in reference["text"]

    def test_switching_translation_relabels_what_is_queued_next(self, client):
        detect(client)  # queued under KJV
        use_version(client, "ltb")
        assert pending(client) == []  # a version change drops the queue (existing rule)
        reference_id = detect(client)
        reference = client.post("/api/approve", json={"reference_id": reference_id}).json()[
            "reference"
        ]
        assert reference["book_label"] == "Jono evangelija"

    def test_a_book_without_a_name_keeps_the_canonical_name(self, client):
        use_version(client, "ltb")
        detect(client, book="Mark", chapter=1, verse=1)
        assert pending(client)[0]["book_label"] is None

    def test_the_label_is_never_used_to_find_the_passage(self, client):
        use_version(client, "ltb")
        reference_id = detect(client, book="Psalms", chapter=23, verse=1)
        reference = client.post("/api/approve", json={"reference_id": reference_id}).json()[
            "reference"
        ]
        assert reference["book"] == "Psalms"
        assert reference["book_label"] == "Psalmynas"
        assert reference["text"]  # resolved from the canonical name

    def test_lookup_still_takes_the_canonical_name_only(self, client):
        use_version(client, "ltb")
        passage = {"chapter": 3, "verse_start": 16}
        assert client.get("/api/lookup", params={"book": "John", **passage}).status_code == 200
        assert (
            client.get("/api/lookup", params={"book": "Jono evangelija", **passage}).status_code
            == 404
        )

    def test_a_submitted_label_cannot_reach_the_screen(self, client):
        use_version(client, "ltb")
        response = client.post(
            "/api/reference",
            json={
                "book": "John",
                "book_label": "Anything the sender likes",
                "chapter": 3,
                "verse_start": 16,
                "confidence": "high",
            },
        )
        assert response.status_code == 201
        assert pending(client)[0]["book_label"] == "Jono evangelija"

    def test_a_passage_chosen_by_hand_is_labelled_too(self, client):
        use_version(client, "ltb")
        with client.websocket_connect("/ws/operator") as operator:
            assert json.loads(operator.receive_text())["type"] == "state_snapshot"
            operator.send_text(
                json.dumps(
                    {
                        "action": "display_reference",
                        "book": "John",
                        "chapter": 3,
                        "verse_start": 16,
                        "verse_end": 16,
                    }
                )
            )
            message = {}
            while message.get("type") != "display_confirmed":
                message = json.loads(operator.receive_text())
        shown = message["data"]["reference"]
        assert shown["book"] == "John"
        assert shown["book_label"] == "Jono evangelija"


class TestSwitchingTranslationWithStateOnScreen:
    """What a translation switch does to labels already in the queue, on screen, and in history."""

    def test_queue_is_dropped_and_screen_and_history_keep_their_own_label_and_text(self, client):
        use_version(client, "ltb")
        first = detect(client)
        detect(client, book="Psalms", chapter=23, verse=1)
        detect(client, chapter=3, verse=17)
        client.post("/api/approve", json={"reference_id": first})
        assert len(pending(client)) == 2

        use_version(client, "kjv")

        state = client.get("/api/state").json()
        # Queued detections were checked against the old text: the switch drops them
        # (existing rule), so none can show a Lithuanian name over English text.
        assert state["pending"] == []
        # What was shown stays exactly as it was shown: name and text from one Bible.
        for shown in (state["current_display"], state["history"][0]):
            assert shown["book_label"] == "Jono evangelija"
            assert "Dievas" in shown["text"]

    def test_showing_it_again_labels_it_from_the_translation_loaded_now(self, client):
        use_version(client, "ltb")
        client.post("/api/approve", json={"reference_id": detect(client)})
        use_version(client, "kjv")

        with client.websocket_connect("/ws/operator") as operator:
            assert json.loads(operator.receive_text())["type"] == "state_snapshot"
            operator.send_text(
                json.dumps(
                    {
                        "action": "display_reference",
                        "book": "John",
                        "chapter": 3,
                        "verse_start": 16,
                        "verse_end": 16,
                    }
                )
            )
            message = {}
            while message.get("type") != "display_confirmed":
                message = json.loads(operator.receive_text())
        shown = message["data"]["reference"]
        assert shown["book_label"] is None
        assert "loved the world" in shown["text"]


class TestFollowAlongLabel:
    def offer(self, client, monkeypatch) -> FollowAlongHint | None:
        state = client.app.state.app_state
        state.current_display = BibleReference(book="John", chapter=3, verse_start=16, verse_end=16)
        state.follow_along = FollowAlong()

        class NoReading:
            def scores(self, *_args):
                return {}

            def ordered(self, *_args):
                return {}

            def idf(self, *_args):
                return 0.0

        async def an_index(_language):
            return NoReading()

        monkeypatch.setattr(state.verse_indexes, "for_language", an_index)
        monkeypatch.setattr(
            "core.server.follow_along.next_verse_hint",
            lambda *_args: FollowAlongHint(
                book="John",
                chapter=3,
                verse_start=17,
                verse_end=17,
                display_id=state.current_display.id,
            ),
        )
        asyncio.run(state.offer_follow_along("anything", "lt"))
        return state.follow_along.last_hint

    def test_the_hint_is_labelled_by_the_loaded_translation(self, client, monkeypatch):
        use_version(client, "ltb")
        hint = self.offer(client, monkeypatch)
        assert hint is not None
        assert hint.book == "John" and hint.book_label == "Jono evangelija"

    def test_the_hint_has_no_label_under_english(self, client, monkeypatch):
        hint = self.offer(client, monkeypatch)
        assert hint is not None and hint.book_label is None
