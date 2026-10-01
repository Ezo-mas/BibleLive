"""Integration tests: BibleLookup + ReferenceParser against a freshly
imported temporary SQLite database (via core.import_bible.BibleImporter).
"""

import pytest

from core.bible_lookup import BibleLookup
from core.verse_parser import ReferenceParser, StreamingParser

pytestmark = pytest.mark.integration


def test_missing_database_raises(tmp_path):
    """BibleLookup must fail fast on a missing database, not create one."""
    with pytest.raises(FileNotFoundError):
        BibleLookup(str(tmp_path / "missing.sqlite"))


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
