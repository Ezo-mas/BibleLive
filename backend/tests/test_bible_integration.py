"""Integration tests: BibleLookup + ReferenceParser against a freshly
imported temporary SQLite database (via core.import_bible.BibleImporter).
"""

import pytest

from core.bible_lookup import BibleLookup, clean_verse_text
from core.verse_parser import ReferenceParser, StreamingParser

pytestmark = pytest.mark.integration


def test_missing_database_raises(tmp_path):
    """BibleLookup must fail fast on a missing database, not create one."""
    with pytest.raises(FileNotFoundError):
        BibleLookup(str(tmp_path / "missing.sqlite"))


def test_schema_migration_adds_missing_columns(tmp_path):
    """An older-schema database gains new columns instead of failing on import."""
    import sqlite3

    from core.import_bible import BibleImporter

    db_path = tmp_path / "legacy.sqlite"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        "CREATE TABLE books ("
        "id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, chapter_count INTEGER)"
    )
    legacy.execute(
        """CREATE TABLE verses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            chapter INTEGER NOT NULL,
            verse INTEGER NOT NULL,
            text TEXT NOT NULL
        )"""
    )
    legacy.execute("INSERT INTO books (name, chapter_count) VALUES ('Genesis', 50)")
    legacy.commit()
    legacy.close()

    importer = BibleImporter(str(db_path))
    importer.connect()
    try:
        importer.create_schema()
        columns = {row[1] for row in importer.conn.execute("PRAGMA table_info(books)")}
        assert {"testament", "order_index"} <= columns
        # Pre-existing rows survive the migration
        assert importer.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    finally:
        importer.close()


def test_bible_lookup(bible_db):
    """Imported database supports validation, text lookup, and counts."""
    bible = BibleLookup(str(bible_db))
    try:
        assert bible.is_loaded()
        assert bible.ping()
        assert bible.book_count() == 2

        # Validation
        assert bible.validate_reference("Genesis", 1, 1)
        assert bible.validate_reference("Genesis", 1, 5)
        assert bible.validate_reference("Genesis", 2, 1)
        assert bible.validate_reference("John", 3, 16)
        assert bible.validate_reference("John", 3)  # chapter-level

        # Out-of-range / unknown references are rejected
        assert not bible.validate_reference("Genesis", 1, 50)
        assert not bible.validate_reference("Genesis", 51, 1)
        assert not bible.validate_reference("John", 3, 18)
        assert not bible.validate_reference("UnknownBook", 1, 1)

        # Verse text
        text = bible.get_verse_text("Genesis", 1, 1, 1)
        assert text == "In the beginning God created the heaven and the earth."
        ranged = bible.get_verse_text("Genesis", 1, 1, 3)
        assert ranged.startswith("In the beginning")
        assert "Let there be light" in ranged

        # Metadata recorded by the importer
        metadata = bible.get_metadata()
        assert metadata["translation"] == "sample"
    finally:
        bible.close()


def test_parser_with_validator(bible_db):
    """ReferenceParser filters references against the loaded database."""
    bible = BibleLookup(str(bible_db))
    try:
        parser = ReferenceParser(validator=bible.get_validator_hook())

        refs = parser.parse("Turn to Genesis 1:1")
        assert [str(r) for r in refs] == ["Genesis 1:1"]

        # Verse outside imported data is dropped by the validator
        assert parser.parse("Genesis 1:99") == []

        # Chapter-level reference survives when the chapter exists
        refs = parser.parse("John chapter three")
        assert [str(r) for r in refs] == ["John 3"]
    finally:
        bible.close()


def test_streaming_context(bible_db):
    """StreamingParser carries book/chapter context between segments."""
    bible = BibleLookup(str(bible_db))
    try:
        parser = ReferenceParser(validator=bible.get_validator_hook())
        stream = StreamingParser(parser)

        stream.feed("Let's turn to Genesis chapter 1")
        refs = stream.feed("Now look at verse 5")
        assert [str(r) for r in refs] == ["Genesis 1:5"]

        stream.reset()
        stream.feed("John chapter three")
        refs = stream.feed("What does verse 16 say?")
        assert [str(r) for r in refs] == ["John 3:16"]
    finally:
        bible.close()


class TestCleanVerseText:
    """Editorial markup from KJV sources must not reach the display."""

    def test_keeps_supplied_word_from_braces(self):
        """``{was}`` is a supplied word: the word belongs in the text."""
        assert (
            clean_verse_text("and darkness {was} upon the face of the deep")
            == "and darkness was upon the face of the deep"
        )

    def test_keeps_supplied_word_from_brackets(self):
        assert clean_verse_text("Blessed [are] the undefiled") == "Blessed are the undefiled"

    def test_drops_marginal_note(self):
        """``{firmament: Heb. expansion}`` is a footnote, not scripture."""
        assert (
            clean_verse_text("And let it divide the waters {from the waters: Heb. division}")
            == "And let it divide the waters"
        )

    def test_drops_red_letter_guillemets(self):
        assert clean_verse_text("‹For God so loved.›") == "For God so loved."

    def test_drops_pilcrow_prefix(self):
        assert clean_verse_text("¶ In the beginning") == "In the beginning"

    def test_keeps_supplied_words_in_a_real_verse(self):
        text = clean_verse_text("And we know that all things work together for good to the called")
        assert text == "And we know that all things work together for good to the called"

    def test_collapses_whitespace_left_by_removals(self):
        assert clean_verse_text("And God said   {saw}   the light") == "And God said saw the light"

    def test_handles_empty_text(self):
        assert clean_verse_text("") == ""
        assert clean_verse_text("   ") == ""

    def test_keeps_text_without_markup(self):
        assert clean_verse_text("plain text") == "plain text"

    def test_leaves_unbalanced_braces_alone(self):
        # Never delete surrounding text just because markup is malformed.
        assert clean_verse_text("a {broken mark") == "a {broken mark"


class TestGetVerseRange:
    """get_verse_range returns numbered verses for bounded display."""

    @pytest.fixture
    def lookup(self, bible_db):
        instance = BibleLookup(str(bible_db))
        yield instance
        instance.close()

    def test_returns_verse_numbers_and_text(self, lookup):
        verses = lookup.get_verse_range("John", 3, 16, 17)
        assert [verse for verse, _ in verses] == [16, 17]
        assert "For God so loved" in verses[0][1]

    def test_single_verse_when_end_is_none(self, lookup):
        assert len(lookup.get_verse_range("John", 3, 16)) == 1

    def test_skips_empty_verses(self, lookup):
        # Genesis 1:4 is an empty string in the sample fixture.
        assert all(text for _, text in lookup.get_verse_range("Genesis", 1, 1, 5))

    def test_reversed_range_is_tolerated(self, lookup):
        forward = lookup.get_verse_range("John", 3, 16, 17)
        assert lookup.get_verse_range("John", 3, 17, 16) == forward

    def test_unknown_book_returns_empty(self, lookup):
        assert lookup.get_verse_range("Nowhere", 1, 1) == []

    def test_out_of_range_verse_returns_empty(self, lookup):
        assert lookup.get_verse_range("John", 3, 999) == []

    def test_get_verse_text_joins_range(self, lookup):
        text = lookup.get_verse_text("John", 3, 16, 17)
        assert "For God so loved" in text
        assert "For God sent" in text


def test_validation_cache_stays_bounded(bible_db):
    """Distinct caller-controlled triples must not grow the cache without limit."""
    from core.bible_lookup import _VALIDATION_CACHE_MAX_ENTRIES

    bible = BibleLookup(str(bible_db))
    try:
        for i in range(_VALIDATION_CACHE_MAX_ENTRIES + 500):
            assert not bible.validate_reference(f"NoSuchBook{i}", 1, 1)
        assert len(bible._validation_cache) <= _VALIDATION_CACHE_MAX_ENTRIES
        # Eviction only drops entries; answers stay right either way.
        assert bible.validate_reference("Genesis", 1, 1)
        assert not bible.validate_reference("Genesis", 1, 50)
        assert len(bible._validation_cache) <= _VALIDATION_CACHE_MAX_ENTRIES
    finally:
        bible.close()
