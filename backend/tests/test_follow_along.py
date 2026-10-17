"""Follow-along: the hint operators get for the verse a speaker reads after the passage on screen.

The spoken-language text is synthetic. ``spoken_db`` is a tiny SQLite file holding
John 3 with distinctive words in a few verses and a common phrase elsewhere, so
only the distinctive words count as heard. The active Bible is the KJV test
database from conftest, which is what the page-size clamp and the "verse exists"
check run against. Nothing here reads bible_data/.
"""

import asyncio
import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from core.bible_lookup import BibleLookup
from core.bible_versions import BibleVersion
from core.config import config
from core.import_bible import BibleImporter
from core.server.follow_along import VerseIndexes, next_verse_hint
from core.server.models import BibleReference, FollowAlongHint
from core.verse_text import VerseTextIndex

# Verses 1-15 and 26-35 repeat FILLER, so its words are common (IDF below 2) and never
# count as heard. Every other verse has words of its own, which only it contains.
FILLER = "Dievas kūrė dangų ir žemę"
SPOKEN = {
    16: "išmanusis valdytojas apkaltintas mokiniams",
    17: "šeimininkas reikalauja sąskaitų atsiskaitymo",
    18: "skolininkams sumažino skolas aliejaus statinės",
    19: "pinigų vaikai nusprendė atlyginti pagrįstai",
    20: "kviečių gerai pastatyti sandėlį",
    21: "karaliaus pasveikinimas miestelyje",
    22: "bemečiai ganytojai slėnyje",
    23: "upelio pakrantėje susirinko",
    24: "akmenys surinkti tvirtovei",
    25: "dykumoje pasirodė angelas",
    36: "paskutinis žodis sakytas",
    37: "šventasis kalnas nepažintas",  # John 3 has no verse 37 in the KJV
}


@pytest.fixture
def auth_enabled():
    """Enable operator authentication for one test (mirrors test_manual_reference)."""
    original_require = config.REQUIRE_AUTH
    original_token = config.OPERATOR_TOKEN

    config.REQUIRE_AUTH = True
    config.OPERATOR_TOKEN = "follow-along-token"

    yield "follow-along-token"

    config.REQUIRE_AUTH = original_require
    config.OPERATOR_TOKEN = original_token


@pytest.fixture
def spoken_db(tmp_path: Path) -> Path:
    """A synthetic Lithuanian John 3 (verses 1-37) in the real import schema."""
    db = tmp_path / "bible_ltb.sqlite"
    importer = BibleImporter(str(db))
    importer.connect()
    try:
        importer.create_schema()
    finally:
        importer.close()
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO books (id, name) VALUES (1, 'John')")
        conn.executemany(
            "INSERT INTO verses (book_id, chapter, verse, text) VALUES (1, 3, ?, ?)",
            [(verse, SPOKEN.get(verse, FILLER)) for verse in range(1, 38)],
        )
    return db


@pytest.fixture
def spoken(app_state, spoken_db, monkeypatch) -> Path:
    """Follow-along reads its Lithuanian index from ``spoken_db``."""
    versions = {"ltb": BibleVersion(id="ltb", path=spoken_db)}
    monkeypatch.setattr(app_state, "verse_indexes", VerseIndexes(lambda: versions))
    return spoken_db


@pytest.fixture
def operator(client, spoken):
    """An operator socket past its initial snapshot."""
    with client.websocket_connect("/ws/operator") as operator_ws:
        assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
        yield operator_ws


def _show(operator_ws, verse_start: int = 16, verse_end: int | None = None) -> dict:
    """Show John 3 by hand (the manual door) and return the confirmed reference."""
    message = {
        "action": "display_reference",
        "book": "John",
        "chapter": 3,
        "verse_start": verse_start,
    }
    if verse_end is not None:
        message["verse_end"] = verse_end
    operator_ws.send_text(json.dumps(message))
    confirmed = json.loads(operator_ws.receive_text())
    assert confirmed["type"] == "display_confirmed"
    return confirmed["data"]["reference"]


# An unknown action is answered with an error on the sending socket alone. Operator
# confirmations (display_settings_confirmed and the like) are broadcast to every
# operator, so they cannot mark the end of one socket's queue.
_MARKER = {"action": "no_such_action"}


def _post_transcript(client, operator_ws, text: str, language: str = "lt") -> list[dict]:
    """Post one transcript; return the follow-along hints operators received for it.

    A hint is sent before the POST returns. The marker sent afterwards gets its
    error back only on ``operator_ws``, which marks the end of what the POST put on it.
    """
    response = client.post("/api/transcript", json={"text": text, "language": language})
    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    operator_ws.send_text(json.dumps(_MARKER))
    hints = []
    while True:
        message = json.loads(operator_ws.receive_text())
        if message["type"] == "error":
            assert message["data"]["action"] == "no_such_action", message
            return hints
        assert message["type"] == "follow_along", message
        hints.append(message["data"])


class TestTranscriptEndpoint:
    """``POST /api/transcript`` takes a transcript and answers with a bare success."""

    def test_requires_the_operator_token_when_auth_is_on(self, client, spoken, auth_enabled):
        body = {"text": SPOKEN[17], "language": "lt"}
        assert client.post("/api/transcript", json=body).status_code == 401
        wrong = {"Authorization": "Bearer nope"}
        assert client.post("/api/transcript", json=body, headers=wrong).status_code == 401

        right = {"Authorization": f"Bearer {auth_enabled}"}
        assert client.post("/api/transcript", json=body, headers=right).status_code == 200

    @pytest.mark.parametrize(
        "body",
        [
            {"language": "lt"},  # text missing
            {"text": "", "language": "lt"},  # text below 1 character
            {"text": "x" * 2001, "language": "lt"},  # text above 2000 characters
            {"text": SPOKEN[17]},  # language missing
            {"text": SPOKEN[17], "language": ""},  # language below 1 character
            {"text": SPOKEN[17], "language": "x" * 17},  # language above 16 characters
            {"text": 17, "language": "lt"},  # text is not a string
        ],
    )
    def test_rejects_bodies_outside_the_schema(self, client, body):
        assert client.post("/api/transcript", json=body).status_code == 422

    def test_accepts_the_longest_allowed_text_and_language(self, client):
        response = client.post("/api/transcript", json={"text": "x" * 2000, "language": "l" * 16})
        assert response.status_code == 200
        assert response.json() == {"status": "success"}

    def test_transcript_is_not_logged(self, client, operator, caplog):
        caplog.set_level(logging.DEBUG)
        _show(operator, 16)
        _post_transcript(client, operator, SPOKEN[17])
        assert SPOKEN[17] not in caplog.text

    def test_flag_off_succeeds_and_does_nothing(self, client, app_state, operator, monkeypatch):
        loads = []
        monkeypatch.setattr(config, "FOLLOW_ALONG", False)
        monkeypatch.setattr(
            app_state,
            "verse_indexes",
            VerseIndexes(lambda: loads.append("loaded") or {}),
        )
        _show(operator, 16)
        assert _post_transcript(client, operator, SPOKEN[17]) == []
        assert loads == []  # not even the index is loaded


class TestHints:
    """What operators are told about the verse the speaker has reached."""

    def test_next_verse_is_offered_to_operators(self, client, operator, app_state):
        display = _show(operator, 16)
        hints = _post_transcript(client, operator, SPOKEN[17])
        assert hints == [
            {
                "book": "John",
                "book_label": None,  # English: no translated name to show
                "chapter": 3,
                "verse_start": 17,
                "verse_end": 17,
                "display_id": display["id"],
            }
        ]
        assert app_state.current_display.verse_start == 16  # nothing moved on screen

    def test_only_operators_get_the_hint_never_a_display(self, client, spoken):
        with (
            client.websocket_connect("/ws/display") as display_ws,
            client.websocket_connect("/ws/operator") as operator_ws,
        ):
            assert json.loads(display_ws.receive_text())["type"] == "clear"
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

            _show(operator_ws, 16)
            assert json.loads(display_ws.receive_text())["type"] == "reference"

            assert len(_post_transcript(client, operator_ws, SPOKEN[17])) == 1

            # The display's next message is the theme change, not the hint.
            operator_ws.send_text(json.dumps({"action": "set_display_theme", "theme": "light"}))
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"

        # Hints are transient: history holds only the passage the operator showed.
        state = client.get("/api/state").json()
        assert state["history_count"] == 1
        assert "follow_along" not in state

    def test_no_hint_without_a_passage_on_screen(self, client, operator):
        assert _post_transcript(client, operator, SPOKEN[17]) == []

    def test_no_hint_for_a_display_without_a_verse(self, client, app_state, operator):
        _show(operator, 16)
        app_state.current_display = app_state.current_display.model_copy(
            update={"verse_start": None}
        )
        assert _post_transcript(client, operator, SPOKEN[17]) == []

    def test_no_hint_below_the_threshold(self, client, operator):
        _show(operator, 16)
        assert _post_transcript(client, operator, FILLER) == []  # common words only
        assert _post_transcript(client, operator, "šeimininkas") == []  # one word of four

    def test_only_the_eight_verses_after_the_passage_are_offered(self, client, operator):
        _show(operator, 16)
        assert _post_transcript(client, operator, SPOKEN[16]) == []  # on screen already
        assert _post_transcript(client, operator, SPOKEN[25]) == []  # nine verses ahead
        hints = _post_transcript(client, operator, SPOKEN[24])  # eight ahead
        assert [(hint["verse_start"], hint["verse_end"]) for hint in hints] == [(24, 24)]

    def test_same_verse_is_not_offered_twice(self, client, operator):
        _show(operator, 16)
        assert len(_post_transcript(client, operator, SPOKEN[17])) == 1
        assert _post_transcript(client, operator, SPOKEN[17]) == []
        assert len(_post_transcript(client, operator, SPOKEN[18])) == 1
        # Back to 17 after 18 is a change again.
        assert len(_post_transcript(client, operator, SPOKEN[17])) == 1

    def test_the_same_passage_shown_again_is_offered_again(self, client, operator):
        first = _show(operator, 16)
        assert len(_post_transcript(client, operator, SPOKEN[17])) == 1

        second = _show(operator, 16)  # a new display, so a new id
        hints = _post_transcript(client, operator, SPOKEN[17])
        assert [hint["display_id"] for hint in hints] == [second["id"]]
        assert second["id"] != first["id"]

    def test_hint_keeps_the_operators_page_size(self, client, operator):
        display = _show(operator, 16, 17)  # two verses on screen
        hints = _post_transcript(client, operator, SPOKEN[18])
        assert hints[0]["verse_start"] == 18
        assert hints[0]["verse_end"] == 19
        assert hints[0]["display_id"] == display["id"]

    def test_hint_stops_at_the_end_of_the_chapter_in_the_active_bible(self, client, operator):
        _show(operator, 34, 35)
        # Verse 36 is the last of John 3 in the KJV; the page would run on to 37.
        hints = _post_transcript(client, operator, SPOKEN[36])
        assert [(hint["verse_start"], hint["verse_end"]) for hint in hints] == [(36, 36)]

    def test_verse_missing_from_the_active_bible_is_not_offered(self, client, operator):
        _show(operator, 30)
        assert [hint["verse_start"] for hint in _post_transcript(client, operator, SPOKEN[36])] == [
            36
        ]
        # Verse 37 is in the Lithuanian text but not in the KJV the server displays.
        assert _post_transcript(client, operator, SPOKEN[37]) == []

    def test_language_code_is_normalised(self, client, operator):
        _show(operator, 16)
        assert len(_post_transcript(client, operator, SPOKEN[17], language=" LT ")) == 1


class TestVerseIndexes:
    """Spoken-language indexes load once, and a failure is remembered, not retried."""

    def test_missing_translation_is_logged_once_and_is_not_fatal(
        self, client, app_state, operator, monkeypatch, caplog
    ):
        monkeypatch.setattr(app_state, "verse_indexes", VerseIndexes(lambda: {}))
        _show(operator, 16)
        with caplog.at_level(logging.WARNING, logger="core.server.follow_along"):
            assert _post_transcript(client, operator, SPOKEN[17]) == []
            assert _post_transcript(client, operator, SPOKEN[17]) == []

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "'ltb' is not installed" in warnings[0].getMessage()

    def test_unreadable_translation_is_logged_once_and_is_not_fatal(
        self, client, app_state, operator, monkeypatch, caplog, tmp_path
    ):
        broken = tmp_path / "bible_ltb.sqlite"
        broken.write_bytes(b"this is not a database")
        versions = {"ltb": BibleVersion(id="ltb", path=broken)}
        monkeypatch.setattr(app_state, "verse_indexes", VerseIndexes(lambda: versions))
        _show(operator, 16)
        with caplog.at_level(logging.WARNING, logger="core.server.follow_along"):
            assert _post_transcript(client, operator, SPOKEN[17]) == []
            assert _post_transcript(client, operator, SPOKEN[17]) == []

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "could not read bible_ltb.sqlite" in warnings[0].getMessage()

    def test_transcripts_arriving_together_share_one_load(self, spoken_db):
        looked = []

        def find_versions():
            looked.append(True)
            return {"ltb": BibleVersion(id="ltb", path=spoken_db)}

        indexes = VerseIndexes(find_versions)

        async def first_three():
            return await asyncio.gather(*(indexes.for_language("lt") for _ in range(3)))

        results = asyncio.run(first_three())
        assert len(looked) == 1
        assert results[0] is not None
        assert all(result is results[0] for result in results)

    def test_a_language_without_a_translation_never_loads(self):
        looked = []
        indexes = VerseIndexes(lambda: looked.append(True) or {})
        assert asyncio.run(indexes.for_language("de")) is None
        assert looked == []


class TestLastVerse:
    """The chapter end that caps a hint comes from the active Bible."""

    def test_reads_the_highest_stored_verse(self, app_state):
        lookup = app_state.bible_lookup
        assert lookup.last_verse("John", 3) == 36
        assert lookup.last_verse("Psalms", 119) == 176

    def test_unknown_book_or_chapter_has_none(self, app_state):
        lookup = app_state.bible_lookup
        assert lookup.last_verse("John", 99) is None
        assert lookup.last_verse("Hezekiah", 1) is None


# Acts 8 as a translation without verses 37 and 38 (WEB has no Acts 8:37).
GAP_STORED_VERSES = [31, 32, 33, 34, 35, 36, 39, 40]
GAP_SPOKEN = {
    33: "etiopijos ministras skaitė",
    34: "pranašas kalbėjo apie",
    35: "avinėlis tylėjo prieš",
    36: "vandens telkinys pasirodė netoli",
    37: "krikštas įvyko greitai",
    38: "filipas nuėjo kartu",
    39: "azotas atvyko į miestą",
    40: "cezarėja sustojo pamokslas",
}


@pytest.fixture
def gap_bible(tmp_path: Path):
    """The active Bible, with Acts 8 missing verses 37 and 38."""
    db = tmp_path / "acts_gap.sqlite"
    importer = BibleImporter(str(db))
    importer.connect()
    try:
        importer.create_schema()
    finally:
        importer.close()
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO books (id, name) VALUES (1, 'Acts')")
        conn.executemany(
            "INSERT INTO verses (book_id, chapter, verse, text) VALUES (1, 8, ?, ?)",
            [(verse, f"Acts 8:{verse}") for verse in GAP_STORED_VERSES],
        )
    lookup = BibleLookup(str(db), readonly=True)
    yield lookup
    lookup.close()


def _gap_index() -> VerseTextIndex:
    """Spoken-language Acts 8 (every verse present) plus filler so the IDF is meaningful."""
    filler = [("Acts", 1, verse, FILLER) for verse in range(1, 31)]
    return VerseTextIndex(filler + [("Acts", 8, verse, text) for verse, text in GAP_SPOKEN.items()])


class TestMissingVersesInTheActiveBible:
    """A hint's end must be a verse the active Bible has, or its Show click fails."""

    @pytest.mark.parametrize(
        ("page_start", "page_end", "expected_end"),
        [
            (34, 35, 36),  # two verses on screen: 36-37 ends on the missing 37, so 36-36
            (33, 35, 36),  # three verses: 36-38 has two missing ends, so it steps back to 36
            (32, 35, 39),  # four verses: 36-39 ends on a verse that exists, gap inside is kept
        ],
    )
    def test_end_steps_back_over_missing_verses(
        self, gap_bible, page_start, page_end, expected_end
    ):
        display = BibleReference(
            id="page",
            book="Acts",
            chapter=8,
            verse_start=page_start,
            verse_end=page_end,
        )
        hint = next_verse_hint(_gap_index(), gap_bible, display, GAP_SPOKEN[36])
        assert hint is not None
        assert (hint.verse_start, hint.verse_end) == (36, expected_end)
        # Both ends of the hint exist in the active Bible, so Show can resolve them.
        assert gap_bible.validate_reference("Acts", 8, hint.verse_start)
        assert gap_bible.validate_reference("Acts", 8, hint.verse_end)


def _assert_nothing_queued(operator_ws) -> None:
    """Prove a socket has no message waiting: its next one is the marker's error."""
    operator_ws.send_text(json.dumps(_MARKER))
    message = json.loads(operator_ws.receive_text())
    assert message["type"] == "error", message
    assert message["data"]["action"] == "no_such_action"


class TestHintAfterReconnect:
    """The hint still on screen is resent to an operator who connects, and only then."""

    def test_a_later_operator_gets_the_snapshot_then_the_hint(self, client, operator):
        _show(operator, 16)
        [hint] = _post_transcript(client, operator, SPOKEN[17])
        with client.websocket_connect("/ws/operator") as late:
            assert json.loads(late.receive_text())["type"] == "state_snapshot"
            assert json.loads(late.receive_text()) == {"type": "follow_along", "data": hint}
            # The hint went to the new socket only; the first console got nothing extra.
            _assert_nothing_queued(operator)

    def test_no_hint_once_the_display_has_changed(self, client, operator):
        _show(operator, 16)
        _post_transcript(client, operator, SPOKEN[17])
        _show(operator, 18)  # a different passage is on screen now
        with client.websocket_connect("/ws/operator") as late:
            assert json.loads(late.receive_text())["type"] == "state_snapshot"
            _assert_nothing_queued(late)

    def test_no_hint_once_the_display_is_cleared(self, client, operator):
        _show(operator, 16)
        _post_transcript(client, operator, SPOKEN[17])
        operator.send_text(json.dumps({"action": "clear"}))
        assert json.loads(operator.receive_text())["type"] == "clear_confirmed"
        with client.websocket_connect("/ws/operator") as late:
            assert json.loads(late.receive_text())["type"] == "state_snapshot"
            _assert_nothing_queued(late)

    def test_dedup_survives_a_reconnect(self, client, operator):
        _show(operator, 16)
        _post_transcript(client, operator, SPOKEN[17])
        with client.websocket_connect("/ws/operator") as late:
            assert json.loads(late.receive_text())["type"] == "state_snapshot"
            assert json.loads(late.receive_text())["type"] == "follow_along"

            # The verse already offered is not offered again, to either console.
            assert _post_transcript(client, operator, SPOKEN[17]) == []
            _assert_nothing_queued(late)

            # A new verse is still offered, and reaches the reconnected console too.
            assert len(_post_transcript(client, operator, SPOKEN[18])) == 1
            follow = json.loads(late.receive_text())
            assert (follow["type"], follow["data"]["verse_start"]) == ("follow_along", 18)


class TestSnapshotHintWithoutASocket:
    """A snapshot sent to every operator (no single socket) carries the hint too."""

    def test_the_hint_follows_the_snapshot_to_all_operators(self, app_state, monkeypatch):
        sent = []

        async def record(message):
            sent.append(message)
            return []

        monkeypatch.setattr(app_state.connection_manager, "send_to_operator", record)
        app_state.current_display = BibleReference(
            id="page", book="John", chapter=3, verse_start=16, verse_end=16
        )
        hint = FollowAlongHint(
            book="John", chapter=3, verse_start=17, verse_end=17, display_id="page"
        )
        app_state.last_follow_along = hint

        asyncio.run(app_state.send_operator_state_snapshot())

        assert [message["type"] for message in sent] == ["state_snapshot", "follow_along"]
        assert sent[1] == {"type": "follow_along", "data": hint.model_dump()}

    def test_a_hint_for_an_earlier_display_is_left_out(self, app_state, monkeypatch):
        sent = []

        async def record(message):
            sent.append(message)
            return []

        monkeypatch.setattr(app_state.connection_manager, "send_to_operator", record)
        app_state.current_display = BibleReference(
            id="page", book="John", chapter=3, verse_start=16, verse_end=16
        )
        app_state.last_follow_along = FollowAlongHint(
            book="John", chapter=3, verse_start=17, verse_end=17, display_id="older"
        )

        asyncio.run(app_state.send_operator_state_snapshot())

        assert [message["type"] for message in sent] == ["state_snapshot"]
