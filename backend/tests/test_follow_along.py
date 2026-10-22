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
from core.server.follow_along import (
    FollowAlong,
    HintPolicy,
    VerseIndexes,
    next_verse_hint,
    verse_started,
)
from core.server.models import BibleReference, FollowAlongHint
from core.server.state import FOLLOW_AUTO_MAX_JUMP
from core.verse_text import VerseTextIndex, ordered_stems

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


def _post_and_collect(client, operator_ws, text: str, language: str = "lt") -> tuple[dict, list]:
    """Post one transcript; return the reply body and the follow-along hints operators got.

    A hint is sent before the POST returns. The marker sent afterwards gets its
    error back only on ``operator_ws``, which marks the end of what the POST put on it.
    """
    response = client.post("/api/transcript", json={"text": text, "language": language})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    operator_ws.send_text(json.dumps(_MARKER))
    hints = []
    while True:
        message = json.loads(operator_ws.receive_text())
        if message["type"] == "error":
            assert message["data"]["action"] == "no_such_action", message
            return body, hints
        assert message["type"] == "follow_along", message
        hints.append(message["data"])


def _post_transcript(client, operator_ws, text: str, language: str = "lt") -> list[dict]:
    """Post one transcript; return the follow-along hints operators received for it."""
    _, hints = _post_and_collect(client, operator_ws, text, language)
    return hints


class TestTranscriptEndpoint:
    """``POST /api/transcript`` takes a transcript and answers with success and whether armed."""

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
        assert response.json() == {"status": "success", "follow_along_active": False}

    def test_reply_is_armed_while_a_passage_is_on_screen(self, client, operator):
        _show(operator, 16)
        # Armed on the first sight of the passage, even before the speaker reads anything.
        body, _ = _post_and_collect(client, operator, FILLER)
        assert body == {"status": "success", "follow_along_active": True}

    def test_reply_is_not_armed_without_a_passage_on_screen(self, client, operator):
        body, hints = _post_and_collect(client, operator, SPOKEN[17])
        assert body == {"status": "success", "follow_along_active": False}
        assert hints == []

    def test_reply_is_not_armed_for_a_display_without_a_verse(self, client, app_state, operator):
        _show(operator, 16)
        app_state.current_display = app_state.current_display.model_copy(
            update={"verse_start": None}
        )
        body, _ = _post_and_collect(client, operator, SPOKEN[17])
        assert body == {"status": "success", "follow_along_active": False}

    def test_reply_is_not_armed_when_the_flag_is_off(self, client, operator, monkeypatch):
        monkeypatch.setattr(config, "FOLLOW_ALONG", False)
        _show(operator, 16)
        body, hints = _post_and_collect(client, operator, SPOKEN[17])
        assert body == {"status": "success", "follow_along_active": False}
        assert hints == []

    def test_reply_is_not_armed_when_matching_fails(self, client, app_state, operator, monkeypatch):
        async def fail(*_args):
            raise RuntimeError("verse index unreadable")

        monkeypatch.setattr(app_state, "offer_follow_along", fail)
        _show(operator, 16)
        body, hints = _post_and_collect(client, operator, SPOKEN[17])
        assert body == {"status": "success", "follow_along_active": False}
        assert hints == []

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
        # 17 was offered already, and it is behind the hint for 18 within the hold.
        assert _post_transcript(client, operator, SPOKEN[17]) == []

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
        app_state.follow_along.last_hint = hint

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
        app_state.follow_along.last_hint = FollowAlongHint(
            book="John", chapter=3, verse_start=17, verse_end=17, display_id="older"
        )

        asyncio.run(app_state.send_operator_state_snapshot())

        assert [message["type"] for message in sent] == ["state_snapshot"]


def _spoken_index() -> VerseTextIndex:
    """John 3 in the spoken language, built the way the server builds its index."""
    return VerseTextIndex([("John", 3, verse, SPOKEN.get(verse, FILLER)) for verse in range(1, 38)])


# The page on screen in the policy and arming tests: John 3:16 alone.
PAGE_16 = BibleReference(id="page", book="John", chapter=3, verse_start=16, verse_end=16)


def _hinted(
    bible,
    transcript: str,
    policy: HintPolicy = HintPolicy(),
    *,
    latest: int | None = None,
    offered: frozenset[int] | None = None,
    floor: int | None = None,
) -> int | None:
    """The verse ``next_verse_hint`` offers for ``transcript`` on the page, or None.

    ``latest`` (the most recent verse hinted for the page) counts as offered, as it would
    be in a real run, unless ``offered`` says otherwise.
    """
    if offered is None:
        offered = frozenset() if latest is None else frozenset({latest})
    hint = next_verse_hint(
        _spoken_index(), bible, PAGE_16, transcript, policy, offered, latest, floor
    )
    return None if hint is None else hint.verse_start


class TestHintPolicy:
    """The knobs of ``next_verse_hint``. The defaults are the rule the server shipped with."""

    def test_defaults_offer_the_verse_after_the_page(self, app_state):
        bible = app_state.bible_lookup
        assert _hinted(bible, SPOKEN[17]) == 17
        assert _hinted(bible, SPOKEN[17], latest=10) == 17  # behind the page: no change

    def test_next_min_score_lowers_the_bar_for_the_verse_right_after_the_page(self, app_state):
        bible = app_state.bible_lookup
        one_word = "šeimininkas"  # a quarter of verse 17's distinctive words
        assert _hinted(bible, one_word) is None
        assert _hinted(bible, one_word, HintPolicy(next_min_score=0.2)) == 17

    def test_next_min_score_is_for_the_sequentially_next_verse_only(self, app_state):
        bible = app_state.bible_lookup
        one_of_five = "pinigų"  # a fifth of verse 19's distinctive words
        assert _hinted(bible, one_of_five, HintPolicy(next_min_score=0.1)) is None  # 19 is 3 ahead
        assert _hinted(bible, one_of_five, HintPolicy(next_min_score=0.1), latest=18) == 19

    def test_next_min_score_follows_the_latest_verse_hinted(self, app_state):
        bible = app_state.bible_lookup
        one_of_five = "skolininkams"  # a fifth of verse 18's distinctive words
        policy = HintPolicy(next_min_score=0.15)
        assert _hinted(bible, one_of_five, policy) is None  # 18 is two ahead of the page
        assert _hinted(bible, one_of_five, policy, latest=17) == 18  # next after 17
        # Once 19 is the latest hint, 18 is behind it: the lower bar goes to 20 instead.
        assert _hinted(bible, one_of_five, policy, latest=19) is None

    def test_pick_best_breaks_a_tie_towards_the_lower_verse(self, app_state):
        both = SPOKEN[17] + " " + SPOKEN[18]  # verses 17 and 18 both read in full
        assert _hinted(app_state.bible_lookup, both, HintPolicy(pick="best")) == 17

    def test_pick_furthest_takes_the_highest_verse_that_clears_its_bar(self, app_state):
        bible = app_state.bible_lookup
        both = SPOKEN[17] + " " + SPOKEN[18]
        assert _hinted(bible, both, HintPolicy(pick="furthest")) == 18
        # Verse 18 at two words of five (0.4) does not clear 0.5, so 17 stays the pick.
        text = SPOKEN[17] + " skolas sumažino"
        assert _hinted(bible, text, HintPolicy(pick="furthest")) == 17

    def test_pick_best_keeps_the_higher_score_over_the_furthest_verse(self, app_state):
        bible = app_state.bible_lookup
        text = SPOKEN[17] + " skolininkams sumažino skolas"  # 17 in full, 18 at 0.6
        assert _hinted(bible, text, HintPolicy(pick="best")) == 17
        assert _hinted(bible, text, HintPolicy(pick="furthest")) == 18

    def test_a_floor_holds_back_the_verses_below_it(self, app_state):
        bible = app_state.bible_lookup
        assert _hinted(bible, SPOKEN[17], floor=18) is None
        assert _hinted(bible, SPOKEN[19], floor=18) == 19  # at or above the floor is fine
        assert _hinted(bible, SPOKEN[17]) == 17  # no floor, nothing is held back

    def test_a_verse_already_offered_is_never_offered_again(self, app_state):
        bible = app_state.bible_lookup
        assert _hinted(bible, SPOKEN[17], offered=frozenset({17})) is None
        assert _hinted(bible, SPOKEN[17], latest=17) is None
        assert _hinted(bible, SPOKEN[17], offered=frozenset({18})) == 17

    def test_the_window_counts_from_the_page_not_from_the_latest_hint(self, app_state):
        bible = app_state.bible_lookup
        assert _hinted(bible, SPOKEN[25]) is None  # nine verses after the page
        # A verse hinted at 20 (say, a misheard one) does not move the window on.
        assert _hinted(bible, SPOKEN[25], latest=20) is None
        assert _hinted(bible, SPOKEN[24], latest=20) == 24  # eight after the page
        assert _hinted(bible, SPOKEN[25], HintPolicy(lookahead=9)) == 25
        assert _hinted(bible, SPOKEN[25], HintPolicy(lookahead=4), latest=20) is None


def _on(follow: FollowAlong, bible, transcript: str, now: float, page=PAGE_16):
    """Feed one transcript to ``follow`` with the spoken-language index; return its answer."""
    return follow.on_transcript(_spoken_index(), bible, page, transcript, now)


class TestFollowAlongArming:
    """Whether follow-along is armed, judged on the clock the caller passes in."""

    def test_a_new_display_is_armed(self, app_state):
        follow = FollowAlong()
        assert _on(follow, app_state.bible_lookup, FILLER, 0.0) == (None, True)

    def test_is_armed_for_a_minute_after_the_display_is_first_seen(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, FILLER, 100.0)
        assert _on(follow, bible, FILLER, 159.9) == (None, True)
        assert _on(follow, bible, FILLER, 160.0) == (None, False)

    def test_a_reading_keeps_it_armed_for_thirty_seconds(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, FILLER, 0.0)  # the minute from first sight is over by 70 s
        assert _on(follow, bible, SPOKEN[16], 70.0) == (None, True)  # the page itself is read
        assert _on(follow, bible, FILLER, 99.9)[1] is True
        assert _on(follow, bible, FILLER, 100.0)[1] is False

    def test_a_reading_past_the_window_does_not_arm(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, FILLER, 0.0)
        # Verse 25 is nine after the page; the window ends eight after it, at verse 24.
        assert _on(follow, bible, SPOKEN[25], 70.0) == (None, False)

    def test_a_new_display_starts_its_own_clock(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, FILLER, 0.0)  # the first display, first seen at 0
        second = PAGE_16.model_copy(update={"id": "page-2"})
        assert _on(follow, bible, FILLER, 100.0, page=second) == (None, True)
        assert _on(follow, bible, FILLER, 159.9, page=second)[1] is True
        assert _on(follow, bible, FILLER, 160.0, page=second)[1] is False

    def test_a_new_display_starts_its_own_hints(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        hint, _ = _on(follow, bible, SPOKEN[17], 0.0)
        assert hint is not None and hint.verse_start == 17
        second = PAGE_16.model_copy(update={"id": "page-2"})
        hint, armed = _on(follow, bible, SPOKEN[17], 1.0, page=second)
        assert armed is True
        assert hint is not None and (hint.display_id, hint.verse_start) == ("page-2", 17)
        assert follow.last_hint is hint

    def test_a_new_display_clears_the_last_hint(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, SPOKEN[17], 0.0)
        assert follow.last_hint is not None
        second = PAGE_16.model_copy(update={"id": "page-2"})
        assert _on(follow, bible, FILLER, 1.0, page=second) == (None, True)
        assert follow.last_hint is None

    def test_a_verse_is_not_offered_twice(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        hint, _ = _on(follow, bible, SPOKEN[17], 0.0)
        assert hint is not None and hint.verse_start == 17
        assert _on(follow, bible, SPOKEN[17], 1.0) == (None, True)
        hint, _ = _on(follow, bible, SPOKEN[18], 2.0)
        assert hint is not None and hint.verse_start == 18
        assert _on(follow, bible, SPOKEN[17], 3.0) == (None, True)

    def test_the_policy_given_is_the_one_used(self, app_state):
        follow = FollowAlong(HintPolicy(next_min_score=0.2))
        hint, _ = _on(follow, app_state.bible_lookup, "šeimininkas", 0.0)
        assert hint is not None and hint.verse_start == 17

    def test_without_a_display_or_an_index_it_is_not_armed(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        assert follow.on_transcript(_spoken_index(), bible, None, SPOKEN[17], 0.0) == (None, False)
        no_verse = PAGE_16.model_copy(update={"verse_start": None})
        assert _on(follow, bible, SPOKEN[17], 0.0, page=no_verse) == (None, False)
        assert follow.on_transcript(None, bible, PAGE_16, SPOKEN[17], 0.0) == (None, False)

    def test_clearing_the_display_disarms_it(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        assert _on(follow, bible, FILLER, 0.0) == (None, True)
        assert follow.on_transcript(_spoken_index(), bible, None, FILLER, 1.0) == (None, False)


class TestRegressHold:
    """No going back: a verse below the latest hint waits out ``regress_hold_s``."""

    def test_a_wrong_hint_does_not_block_the_right_lower_verse_for_long(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        hint, _ = _on(follow, bible, SPOKEN[20], 0.0)  # a misheard partial: 20 over the page 16
        assert hint is not None and hint.verse_start == 20
        hint, _ = _on(follow, bible, SPOKEN[17], 20.0)  # 20 s later the speaker is on 17
        assert hint is not None and hint.verse_start == 17

    def test_a_lower_verse_is_held_back_within_the_hold(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, SPOKEN[20], 0.0)
        hint, armed = _on(follow, bible, SPOKEN[17], 5.0)
        assert hint is None and armed is True

    def test_the_hold_ends_regress_hold_s_after_the_hint(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, SPOKEN[20], 0.0)
        assert _on(follow, bible, SPOKEN[17], 14.9)[0] is None
        hint, _ = _on(follow, bible, SPOKEN[17], 15.0)
        assert hint is not None and hint.verse_start == 17

    def test_the_hold_length_is_a_constructor_argument(self, app_state):
        follow, bible = FollowAlong(regress_hold_s=2.0), app_state.bible_lookup
        _on(follow, bible, SPOKEN[20], 0.0)
        assert _on(follow, bible, SPOKEN[17], 1.9)[0] is None
        hint, _ = _on(follow, bible, SPOKEN[17], 2.0)
        assert hint is not None and hint.verse_start == 17

    def test_a_higher_verse_is_still_offered_within_the_hold(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, SPOKEN[18], 0.0)
        hint, _ = _on(follow, bible, SPOKEN[21], 1.0)
        assert hint is not None and hint.verse_start == 21

    def test_a_partial_hint_is_not_stepped_back_by_the_final_of_its_utterance(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        hint, _ = _on(follow, bible, SPOKEN[18], 0.0)  # the partial hints 18
        assert hint is not None and hint.verse_start == 18
        # The final reads 17 and 18 in full; the tie goes to 17, which is held back.
        final = SPOKEN[17] + " " + SPOKEN[18]
        assert _on(follow, bible, final, 3.0)[0] is None
        hint, _ = _on(follow, bible, final, 20.0)  # once the hold is over, 17 is offered
        assert hint is not None and hint.verse_start == 17

    def test_a_verse_offered_after_the_hold_is_not_offered_again(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, SPOKEN[20], 0.0)
        hint, _ = _on(follow, bible, SPOKEN[17], 20.0)
        assert hint is not None and hint.verse_start == 17
        assert _on(follow, bible, SPOKEN[17], 40.0)[0] is None
        assert _on(follow, bible, SPOKEN[20], 40.0)[0] is None

    def test_a_new_display_starts_without_a_hold(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, SPOKEN[20], 0.0)
        second = PAGE_16.model_copy(update={"id": "page-2"})
        hint, _ = _on(follow, bible, SPOKEN[17], 1.0, page=second)
        assert hint is not None and (hint.display_id, hint.verse_start) == ("page-2", 17)

    def test_the_window_does_not_drift_with_the_latest_hint(self, app_state):
        follow, bible = FollowAlong(), app_state.bible_lookup
        _on(follow, bible, SPOKEN[20], 0.0)
        # 25 is nine verses after the page 16, so outside the window however far 20 reaches.
        assert _on(follow, bible, SPOKEN[25], 20.0)[0] is None
        hint, _ = _on(follow, bible, SPOKEN[24], 20.0)  # eight after the page: in the window
        assert hint is not None and hint.verse_start == 24


# Verses of John 3 and 16 in the Lithuanian translation, as the database has them, with the
# soft hyphens of the source left out. Inline, so these tests do not read bible_data/.
START_VERSES = {
    ("John", 3, 3): (
        "Jėzus jam atsakė: “Iš tiesų, iš tiesų sakau tau: jei kas negims iš naujo, "
        "negalės regėti Dievo karalystės”."
    ),
    ("John", 3, 10): "Jėzus jam atsakė: “Tu esi Izraelio mokytojas ir šito nesupranti?",
    ("John", 16, 16): (
        "Prabėgs valandėlė ir manęs nematysite, ir dar valandėlė ir vėl mane pamatysite, "
        "nes Aš pas Tėvą einu”."
    ),
    ("John", 16, 17): (
        "Tada kai kurie mokiniai ėmė vienas kitą klausinėti: “Ką reiškia Jo pasakyti žodžiai: "
        "‘Prabėgs valandėlė ir manęs nematysite, ir dar valandėlė ir vėl mane pamatysite’ ir: "
        "‘Aš pas Tėvą einu’?”"
    ),
    ("John", 16, 18): "Tad jie klausinėjo: “Ką reiškia ‘valandėlė’? Mums neaišku, ką Jis kalba”.",
}


def _rest_of_the_translation() -> list[tuple[str, int, int, str]]:
    """A stand-in for the other 31,000 verses, so the IDF weights are as in production.

    A stem found in one verse of 31,000 weighs about 10, as in the real translation. One verse
    in 33 opens "Jėzus atsakė" (about 3.5 a stem, as the common Gospel opening is); the rest
    say something that every verse says, which weighs about nothing.
    """
    return [
        (
            "Genesis",
            i // 50 + 1,
            i % 50 + 1,
            "Jėzus atsakė jiems dangų ir žemę" if i % 33 == 0 else "Dievas kūrė dangų ir žemę",
        )
        for i in range(31_000)
    ]


@pytest.fixture(scope="module")
def start_index() -> VerseTextIndex:
    """The Lithuanian index for the verse-start tests: START_VERSES among the stand-in verses."""
    rows = _rest_of_the_translation() + [(b, c, v, t) for (b, c, v), t in START_VERSES.items()]
    return VerseTextIndex(rows)


# The page on screen for the verse-start tests: John 16:16. The same-opening tests read from
# John 3:2.
START_PAGE = BibleReference(id="page", book="John", chapter=16, verse_start=16, verse_end=16)
SAME_OPENING_PAGE = BibleReference(id="page", book="John", chapter=3, verse_start=2, verse_end=2)
START_POLICY = HintPolicy(start_match=True)


def _started(start_index, transcript: str, policy: HintPolicy = START_POLICY, verse: int = 17):
    """Whether ``transcript`` reads the start of John 16:``verse``."""
    return verse_started(start_index, "John", 16, verse, ordered_stems(transcript), policy)


def _start_hint(
    start_index, bible, transcript: str, policy: HintPolicy, page=START_PAGE, read_through=None
):
    """The verse ``next_verse_hint`` offers for ``transcript`` on ``page``, or None."""
    hint = next_verse_hint(start_index, bible, page, transcript, policy, read_through=read_through)
    return None if hint is None else hint.verse_start


# The end of John 16:16 (the page on screen), then the start of 16:17.
END_THEN_START = "nes Aš pas Tėvą einu. Tada kai kurie mokiniai"


class TestVerseStart:
    """``verse_started``: the first words of a verse, heard in order (John 16:17 here)."""

    def test_the_first_three_words_of_the_next_verse_start_it(self, start_index):
        assert _started(start_index, "Tada kai kurie mokiniai")
        assert _started(start_index, "Tada kai kurie mokiniai ėmė vienas")

    def test_two_words_do_not_start_it(self, start_index):
        assert not _started(start_index, "Tada kai kurie")
        assert not _started(start_index, "Tada mokiniai")

    def test_a_misheard_word_is_tolerated(self, start_index):
        assert _started(start_index, "Tada kažkurie mokiniai ėmė vienas")

    def test_an_inserted_word_is_tolerated(self, start_index):
        assert _started(start_index, "Tada tikrai kurie mokiniai")
        assert _started(start_index, "Tada brangieji tikrieji kurie mokiniai")  # two inserted

    def test_a_lost_first_word_is_tolerated(self, start_index):
        assert _started(start_index, "kurie mokiniai ėmė vienas")

    def test_words_out_of_order_do_not_start_it(self, start_index):
        assert not _started(start_index, "mokiniai kurie tada")

    def test_three_words_skipped_in_the_verse_break_the_order(self, start_index):
        # "Tada" then "kitą" leaves kurie, mokiniai and ėmė between them: three stems skipped.
        assert not _started(start_index, "Tada kitą klausinėti")

    def test_three_words_inserted_by_the_speaker_break_the_order(self, start_index):
        assert not _started(start_index, "Tada brangieji tikrieji žmonės kurie mokiniai")

    def test_a_run_from_the_middle_of_the_verse_is_not_a_start(self, start_index):
        middle = "ėmė vienas kitą klausinėti"  # the verse's 4th to 6th stems
        assert not _started(start_index, middle)
        assert _started(start_index, middle, HintPolicy(start_match=True, start_anchor=3))

    def test_the_knobs_set_the_bars(self, start_index):
        three = "Tada kai kurie mokiniai"
        four = HintPolicy(start_match=True, start_min_tokens=4)
        assert not _started(start_index, three, four)
        assert _started(start_index, three + " ėmė vienas", four)
        assert not _started(start_index, three, HintPolicy(start_match=True, start_min_idf=40.0))
        # Only the first three stems of the verse count, so "viena" (the fourth) is out of reach.
        short = HintPolicy(start_match=True, start_prefix=3)
        assert not _started(start_index, "Tada kurie viena", short)
        assert _started(start_index, "Tada kurie viena")

    def test_common_words_do_not_start_a_verse_however_many(self, start_index):
        # Genesis 1:1 of the stand-in opens "Jėzus atsakė jiems dangų ir žemę". "dangų ir žemę"
        # is in nearly every verse and weighs nothing, so four stems in order are not enough:
        # the IDF bar needs "jiems" (about 3.5) as well.
        tokens = ordered_stems("Jėzus atsakė dangų ir žemę")
        assert not verse_started(start_index, "Genesis", 1, 1, tokens, START_POLICY)
        tokens = ordered_stems("Jėzus atsakė jiems")
        assert verse_started(start_index, "Genesis", 1, 1, tokens, START_POLICY)

    def test_a_verse_of_another_chapter_or_book_is_not_matched(self, start_index):
        tokens = ordered_stems("Tada kai kurie mokiniai")
        assert not verse_started(start_index, "John", 16, 99, tokens, START_POLICY)
        assert not verse_started(start_index, "John", 15, 17, tokens, START_POLICY)


class TestVerseStartHints:
    """The verse-start rule as ``next_verse_hint`` uses it: a candidate beside the bag rule."""

    def test_on_by_default_and_can_be_turned_off(self, start_index, app_state):
        bible = app_state.bible_lookup
        assert _start_hint(start_index, bible, END_THEN_START, HintPolicy()) == 17
        off = HintPolicy(start_match=False)
        assert _start_hint(start_index, bible, END_THEN_START, off) is None

    def test_a_start_alone_needs_the_verse_before_it_read(self, start_index, app_state):
        # Only the start of 17 is heard: nothing says 16 was being read, so a later verse that
        # merely opens with words of an earlier one cannot pass for its start.
        bible, three = app_state.bible_lookup, "Tada kai kurie mokiniai"
        assert _start_hint(start_index, bible, three, START_POLICY) is None
        # Follow-along saw 16 read (it offered it, or the operator showed that hint).
        assert _start_hint(start_index, bible, three, START_POLICY, read_through=16) == 17

    def test_the_end_of_the_page_then_the_start_of_the_next_verse(self, start_index, app_state):
        # Verse 16 is on screen and its end is read, then the start of 17: the verse whose
        # start was heard is offered.
        text = "nes Aš pas Tėvą einu. Tada kai kurie mokiniai"
        policy = HintPolicy()
        assert _start_hint(start_index, app_state.bible_lookup, text, policy) == 17

    def test_same_openings_only_the_next_verse_can_start(self, start_index, app_state):
        # John 3:3 and 3:10 both open "Jėzus jam atsakė": two stems, weighing about 7 together,
        # short of the bar, so a third stem of the verse completes its start. And only the
        # verse right after the page (3:3 after 3:2) can start: 3:10's opening is not a start.
        bible = app_state.bible_lookup
        policy = HintPolicy()
        page = SAME_OPENING_PAGE

        def hint(text):
            return _start_hint(start_index, bible, text, policy, page, read_through=2)

        assert hint("Jėzus jam atsakė iš tiesų") == 3
        assert hint("Jėzus jam atsakė Izraelio") is None
        assert hint("Jėzus jam atsakė") is None


# Matthew 7:23-27 (LTB): 7:24 and 7:26 are worded alike ("kiekvienas, kuris klauso šitų mano
# žodžių ir juos vykdo / ir jų nevykdo"), and so are 7:25 and 7:27.
MATTHEW_7 = {
    23: "Tada Aš jiems pareikšiu: ‘Aš niekuomet jūsų nepažinojau. Šalin nuo manęs, jūs "
    "piktadariai!’",
    24: "Taigi kiekvienas, kuris klauso šitų mano žodžių ir juos vykdo, panašus į išmintingą "
    "žmogų, pasistačiusį savo namą ant uolos.",
    25: "Prapliupo liūtys, ištvino upės, pakilo vėjai ir daužėsi į tą namą. Tačiau jis "
    "nesugriuvo, nes buvo pastatytas ant uolos.",
    26: "Ir kiekvienas, kuris klauso šitų mano žodžių ir jų nevykdo, panašus į kvailą žmogų, "
    "pasistačiusį savo namą ant smėlio.",
    27: "Prapliupo liūtys, ištvino upės, pakilo vėjai ir daužėsi į tą namą, ir jis sugriuvo, o "
    "jo griuvimas buvo smarkus”.",
}


class _AnyVerseBible:
    """A Bible in which every verse of a 29-verse chapter exists (Matthew 7)."""

    def validate_reference(self, *_args) -> bool:
        return True

    def last_verse(self, *_args) -> int:
        return 29


class TestParallelWording:
    """A later verse worded like the one being read must not be offered in its place."""

    @pytest.fixture(scope="class")
    def matthew_index(self) -> VerseTextIndex:
        rows = _rest_of_the_translation() + [("Matthew", 7, v, t) for v, t in MATTHEW_7.items()]
        return VerseTextIndex(rows)

    def test_reading_7_24_offers_7_24_not_its_parallel_7_26(self, matthew_index):
        page = BibleReference(id="m", book="Matthew", chapter=7, verse_start=23, verse_end=23)
        hint = next_verse_hint(matthew_index, _AnyVerseBible(), page, MATTHEW_7[24])
        assert hint is not None and hint.verse_start == 24
        # The knob that would jump: 7:26 clears the bar on 7:24's words.
        furthest = HintPolicy(pick="furthest")
        jumped = next_verse_hint(matthew_index, _AnyVerseBible(), page, MATTHEW_7[24], furthest)
        assert jumped is not None and jumped.verse_start == 26


class TestVerseStartArming:
    """A start reading counts as reading the passage, for arming and for the once-only rule."""

    def test_a_start_reading_arms_follow_along_after_the_first_minute(self, start_index, app_state):
        policy = HintPolicy()
        follow, bible = FollowAlong(policy), app_state.bible_lookup
        assert follow.on_transcript(start_index, bible, START_PAGE, FILLER, 0.0) == (None, True)
        hint, armed = follow.on_transcript(start_index, bible, START_PAGE, END_THEN_START, 70.0)
        assert armed is True
        assert hint is not None and hint.verse_start == 17

    def test_without_the_start_rule_the_same_reading_does_not_arm(self, start_index, app_state):
        follow, bible = FollowAlong(HintPolicy(start_match=False)), app_state.bible_lookup
        follow.on_transcript(start_index, bible, START_PAGE, FILLER, 0.0)
        reading = "Tada kai kurie mokiniai"
        assert follow.on_transcript(start_index, bible, START_PAGE, reading, 70.0) == (None, False)

    def test_a_start_is_offered_once(self, start_index, app_state):
        policy = HintPolicy()
        follow, bible = FollowAlong(policy), app_state.bible_lookup
        hint, _ = follow.on_transcript(start_index, bible, START_PAGE, END_THEN_START, 0.0)
        assert hint is not None and hint.verse_start == 17
        again = follow.on_transcript(start_index, bible, START_PAGE, END_THEN_START, 10.0)
        assert again == (None, True)

    def test_a_display_of_the_offered_verse_counts_it_as_read(self, start_index, app_state):
        """After a Show (or auto-show) of the hint, the next verse's start can count at once."""
        policy = HintPolicy()
        follow, bible = FollowAlong(policy), app_state.bible_lookup
        follow.on_transcript(start_index, bible, START_PAGE, END_THEN_START, 0.0)
        shown = BibleReference(id="p17", book="John", chapter=16, verse_start=17, verse_end=17)
        follow.on_transcript(start_index, bible, shown, FILLER, 2.0)
        assert follow._carried_read == 17
        # Any other passage starts with nothing carried over.
        other = BibleReference(id="p20", book="John", chapter=16, verse_start=20, verse_end=20)
        follow.on_transcript(start_index, bible, other, FILLER, 3.0)
        assert follow._carried_read is None

    def test_after_showing_the_hint_a_bare_start_of_the_next_verse_is_offered(
        self, start_index, app_state
    ):
        follow, bible = FollowAlong(), app_state.bible_lookup
        follow.on_transcript(start_index, bible, START_PAGE, END_THEN_START, 0.0)
        shown = BibleReference(id="p17", book="John", chapter=16, verse_start=17, verse_end=17)
        hint, _ = follow.on_transcript(
            start_index, bible, shown, "Tad jie klausinėjo ką reiškia valandėlė", 5.0
        )
        assert hint is not None and hint.verse_start == 18

    def test_what_was_read_stops_counting_after_the_arming_hold(self, start_index, app_state):
        """A start heard long after the last verse seen read is a quote, not the reading."""
        follow, bible = FollowAlong(), app_state.bible_lookup
        three = "Tada kai kurie mokiniai"
        # 16 was offered on another page and put up: 16 counts as read, for 30 s.
        page15 = BibleReference(id="p15", book="John", chapter=16, verse_start=15, verse_end=15)
        follow.last_hint = FollowAlongHint(
            book="John", chapter=16, verse_start=16, verse_end=16, display_id="p15"
        )
        follow._display_id = page15.id
        assert follow.on_transcript(start_index, bible, START_PAGE, FILLER, 0.0)[0] is None
        late, _ = follow.on_transcript(start_index, bible, START_PAGE, three, 31.0)
        assert late is None


# Follow-along auto-show: with the operator's switch on, a qualifying hint is shown by the
# server itself through the manual path; with it off, or for anything else, it stays a hint.


def _set_follow_auto(operator_ws, enabled: bool) -> None:
    """Turn the auto-show switch on or off from an operator socket and check the confirmation."""
    operator_ws.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": enabled}))
    confirmed = json.loads(operator_ws.receive_text())
    assert confirmed == {"type": "follow_auto_confirmed", "data": {"follow_auto": enabled}}


def _messages_until_marker(operator_ws) -> list[dict]:
    """Every message an operator socket has been sent since its last read, up to the marker."""
    operator_ws.send_text(json.dumps(_MARKER))
    messages = []
    while True:
        message = json.loads(operator_ws.receive_text())
        if message["type"] == "error":
            assert message["data"]["action"] == "no_such_action", message
            return messages
        messages.append(message)


def _post_all(client, operator_ws, text: str, language: str = "lt") -> tuple[dict, list[dict]]:
    """Post one transcript; return the reply and every operator message it caused."""
    response = client.post("/api/transcript", json={"text": text, "language": language})
    assert response.status_code == 200
    return response.json(), _messages_until_marker(operator_ws)


def _kjv_text(app_state, verse_start: int, verse_end: int | None = None) -> str:
    """The active Bible's text for John 3, joined as the server joins it."""
    rows = app_state.bible_lookup.get_verse_range("John", 3, verse_start, verse_end or verse_start)
    return " ".join(text for _, text in rows)


class TestFollowAuto:
    """With the switch on, the server shows the verse a speaker reads on to by itself."""

    def test_off_by_default_the_next_verse_is_only_offered(self, client, app_state, operator):
        assert app_state.follow_auto is False
        _show(operator, 16)

        body, messages = _post_all(client, operator, SPOKEN[17])

        assert [message["type"] for message in messages] == ["follow_along"]
        assert body == {"status": "success", "follow_along_active": True}
        assert app_state.current_display.verse_start == 16
        assert len(app_state.reference_history) == 1

    def test_switch_on_shows_the_next_verse_by_itself(self, client, app_state, operator):
        _show(operator, 16)
        _set_follow_auto(operator, True)

        body, messages = _post_all(client, operator, SPOKEN[17])

        # The verse goes up through the manual path: its confirmation, and no hint.
        assert [message["type"] for message in messages] == ["display_confirmed"]
        shown = messages[0]["data"]["reference"]
        assert (shown["book"], shown["chapter"], shown["verse_start"], shown["verse_end"]) == (
            "John",
            3,
            17,
            17,
        )
        assert shown["source"] == "follow"
        assert shown["confidence"] is None
        # The text is the server's own Bible text, never anything the transcript carried.
        assert shown["text"] == _kjv_text(app_state, 17)
        assert shown["verses"] == [{"verse": 17, "text": _kjv_text(app_state, 17)}]
        assert app_state.current_display.id == shown["id"]
        assert app_state.reference_history[0].id == shown["id"]
        assert body == {"status": "success", "follow_along_active": True}

    def test_the_display_gets_the_followed_passage_once(self, client, spoken):
        with (
            client.websocket_connect("/ws/display") as display_ws,
            client.websocket_connect("/ws/operator") as operator_ws,
        ):
            assert json.loads(display_ws.receive_text())["type"] == "clear"
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"
            _show(operator_ws, 16)
            assert json.loads(display_ws.receive_text())["type"] == "reference"
            _set_follow_auto(operator_ws, True)

            _, messages = _post_all(client, operator_ws, SPOKEN[17])
            followed = json.loads(display_ws.receive_text())
            assert followed["type"] == "reference"
            assert followed["data"]["source"] == "follow"
            assert followed["data"]["id"] == messages[0]["data"]["reference"]["id"]

            # Nothing else reached the display: its next message is the theme change.
            operator_ws.send_text(json.dumps({"action": "set_display_theme", "theme": "light"}))
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"

    def test_two_consoles_show_the_verse_once(self, client, spoken, app_state):
        with (
            client.websocket_connect("/ws/display") as display_ws,
            client.websocket_connect("/ws/operator") as first,
            client.websocket_connect("/ws/operator") as second,
        ):
            assert json.loads(display_ws.receive_text())["type"] == "clear"
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"
            assert json.loads(first.receive_text())["type"] == "state_snapshot"
            assert json.loads(second.receive_text())["type"] == "state_snapshot"
            _show(first, 16)
            assert json.loads(display_ws.receive_text())["type"] == "reference"
            assert json.loads(second.receive_text())["type"] == "display_confirmed"

            first.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": True}))
            assert json.loads(first.receive_text())["type"] == "follow_auto_confirmed"
            assert json.loads(second.receive_text())["type"] == "follow_auto_confirmed"

            _, first_messages = _post_all(client, first, SPOKEN[17])
            second_messages = _messages_until_marker(second)

            # Both consoles hear about the one passage; the display got one reference.
            assert [message["type"] for message in first_messages] == ["display_confirmed"]
            assert second_messages == first_messages
            assert json.loads(display_ws.receive_text())["type"] == "reference"
            first.send_text(json.dumps({"action": "set_display_theme", "theme": "light"}))
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"
        assert len(app_state.reference_history) == 2

    def test_a_verse_three_past_the_passage_is_shown(self, client, operator, app_state):
        assert FOLLOW_AUTO_MAX_JUMP == 3
        _show(operator, 16)
        _set_follow_auto(operator, True)

        _, messages = _post_all(client, operator, SPOKEN[19])  # three past verse 16

        assert [message["type"] for message in messages] == ["display_confirmed"]
        assert app_state.current_display.verse_start == 19

    def test_a_verse_four_past_the_passage_stays_a_hint(self, client, operator, app_state):
        _show(operator, 16)
        _set_follow_auto(operator, True)

        _, messages = _post_all(client, operator, SPOKEN[20])  # four past verse 16

        assert [message["type"] for message in messages] == ["follow_along"]
        assert messages[0]["data"]["verse_start"] == 20
        assert app_state.current_display.verse_start == 16
        assert len(app_state.reference_history) == 1

    def test_the_jump_is_counted_from_the_end_of_the_passage_on_screen(
        self, client, operator, app_state
    ):
        _show(operator, 16, 17)
        _set_follow_auto(operator, True)

        _, messages = _post_all(client, operator, SPOKEN[20])  # three past verse 17

        assert [message["type"] for message in messages] == ["display_confirmed"]
        shown = messages[0]["data"]["reference"]
        assert (shown["verse_start"], shown["verse_end"]) == (20, 21)
        assert shown["text"] == _kjv_text(app_state, 20, 21)

    def test_a_hint_four_past_the_end_of_a_two_verse_passage_stays_a_hint(
        self, client, operator, app_state
    ):
        _show(operator, 16, 17)
        _set_follow_auto(operator, True)

        _, messages = _post_all(client, operator, SPOKEN[21])  # four past verse 17

        assert [message["type"] for message in messages] == ["follow_along"]
        assert app_state.current_display.verse_start == 16

    def test_the_followed_passage_is_never_pending_and_has_no_confidence(
        self, client, operator, app_state
    ):
        _show(operator, 16)
        _set_follow_auto(operator, True)

        _post_all(client, operator, SPOKEN[17])

        assert app_state.pending_references == []
        assert client.get("/api/state").json()["pending_count"] == 0
        shown = app_state.current_display
        assert shown.source == "follow"
        assert shown.confidence is None
        assert app_state.reference_history[0].confidence is None

    def test_the_followed_passage_goes_through_display_manual_passage(
        self, client, operator, app_state, monkeypatch
    ):
        calls = []
        original = app_state.display_manual_passage

        async def spy(passage, source="manual"):
            calls.append(source)
            return await original(passage, source=source)

        monkeypatch.setattr(app_state, "display_manual_passage", spy)
        _show(operator, 16)
        _set_follow_auto(operator, True)

        _post_all(client, operator, SPOKEN[17])

        assert calls == ["manual", "follow"]

    def test_a_console_that_connects_afterwards_sees_the_followed_passage(
        self, client, operator, app_state
    ):
        _show(operator, 16)
        _set_follow_auto(operator, True)
        _post_all(client, operator, SPOKEN[17])

        with client.websocket_connect("/ws/operator") as late:
            snapshot = json.loads(late.receive_text())["data"]
            assert snapshot["current_display"]["source"] == "follow"
            assert snapshot["follow_auto"] is True
            assert snapshot["history_count"] == 2
            # The hint for the passage that was replaced is not resent.
            _assert_nothing_queued(late)

    def test_a_display_that_changed_meanwhile_is_not_replaced(
        self, client, operator, app_state, monkeypatch
    ):
        shown = _show(operator, 16)
        _set_follow_auto(operator, True)
        decide = app_state.follow_along.on_transcript

        def change_display_after_deciding(*args, **kwargs):
            result = decide(*args, **kwargs)
            # Another passage lands on screen before the decision is acted on.
            app_state.current_display = app_state.current_display.model_copy(
                update={"id": "replaced"}
            )
            return result

        monkeypatch.setattr(app_state.follow_along, "on_transcript", change_display_after_deciding)

        _, messages = _post_all(client, operator, SPOKEN[17])

        # The hint still goes out as a hint, for the passage it was computed on.
        assert [message["type"] for message in messages] == ["follow_along"]
        assert messages[0]["data"]["display_id"] == shown["id"]
        assert app_state.current_display.id == "replaced"
        assert len(app_state.reference_history) == 1

    def test_nothing_is_shown_once_the_display_is_cleared(self, client, operator, app_state):
        _show(operator, 16)
        _set_follow_auto(operator, True)
        operator.send_text(json.dumps({"action": "clear"}))
        assert json.loads(operator.receive_text())["type"] == "clear_confirmed"

        body, messages = _post_all(client, operator, SPOKEN[17])

        assert messages == []
        assert body == {"status": "success", "follow_along_active": False}
        assert app_state.current_display is None

    def test_auto_approve_still_handles_detections_on_its_own(self, client, operator, app_state):
        _show(operator, 16)
        _set_follow_auto(operator, True)

        response = client.post(
            "/api/reference",
            json={
                "book": "John",
                "chapter": 3,
                "verse_start": 18,
                "confidence": "high",
                "text": "",
            },
        )
        assert response.status_code == 201
        _messages_until_marker(operator)
        # Auto-approve is off, so the detection waits; the switch does not approve it.
        assert [ref.verse_start for ref in app_state.pending_references] == [18]
        assert app_state.current_display.verse_start == 16

        operator.send_text(json.dumps({"action": "set_auto_mode", "auto_mode": "all"}))
        assert json.loads(operator.receive_text())["type"] == "auto_mode_confirmed"
        client.post(
            "/api/reference",
            json={
                "book": "John",
                "chapter": 3,
                "verse_start": 19,
                "confidence": "high",
                "text": "",
            },
        )
        _messages_until_marker(operator)
        # Approved as a detection, exactly as before: its source and confidence are its own.
        assert app_state.current_display.source == "detected"
        assert app_state.current_display.confidence == "high"
        assert app_state.follow_auto is True


class TestFollowAutoGate:
    """Which hints the server may show by itself, decided without any socket."""

    @staticmethod
    def _display(app_state, verse_start: int = 16, verse_end: int | None = None) -> None:
        app_state.current_display = BibleReference(
            id="shown",
            book="John",
            chapter=3,
            verse_start=verse_start,
            verse_end=verse_end,
            source="manual",
        )

    @staticmethod
    def _hint(verse_start: int, display_id: str = "shown") -> FollowAlongHint:
        return FollowAlongHint(
            book="John",
            chapter=3,
            verse_start=verse_start,
            verse_end=verse_start,
            display_id=display_id,
        )

    def test_closed_while_the_switch_is_off(self, app_state):
        self._display(app_state)
        assert app_state._follow_auto_passage(self._hint(17)) is None

    def test_open_for_a_hint_within_three_verses(self, app_state):
        app_state.follow_auto = True
        self._display(app_state)
        passage = app_state._follow_auto_passage(self._hint(19))
        assert passage is not None
        assert (passage.book, passage.chapter, passage.verse_start) == ("John", 3, 19)

    def test_closed_for_a_hint_four_verses_ahead(self, app_state):
        app_state.follow_auto = True
        self._display(app_state)
        assert app_state._follow_auto_passage(self._hint(20)) is None

    def test_closed_for_a_hint_that_follows_another_display(self, app_state):
        app_state.follow_auto = True
        self._display(app_state)
        assert app_state._follow_auto_passage(self._hint(17, display_id="older")) is None

    def test_closed_while_nothing_is_on_screen(self, app_state):
        app_state.follow_auto = True
        app_state.current_display = None
        assert app_state._follow_auto_passage(self._hint(17)) is None

    def test_closed_when_the_verse_does_not_resolve(self, app_state):
        # John 3 ends at verse 36 in the active Bible, so 37 is a hint, not a passage.
        app_state.follow_auto = True
        self._display(app_state, verse_start=35)
        assert app_state._follow_auto_passage(self._hint(37)) is None

    def test_closed_while_follow_along_is_turned_off_even_if_saved_on(self, app_state, monkeypatch):
        app_state.follow_auto = True
        monkeypatch.setattr(config, "FOLLOW_ALONG", False)
        self._display(app_state)
        assert app_state._follow_auto_passage(self._hint(17)) is None
