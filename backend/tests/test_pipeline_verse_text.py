"""The Lithuanian verse-text index goes to the Lithuanian parser only, and only if its file exists."""

import pytest
from processor_factory import make_processor

from core.verse_text import VerseTextIndex

# Requires the optional audio extras (faster-whisper, webrtcvad, numpy).
pytestmark = pytest.mark.audio


def _parsers(bible_db, verse_text_db_path):
    """Build the per-language parsers as the transcription thread does."""
    proc = make_processor(bible_db_path=str(bible_db), verse_text_db_path=verse_text_db_path)
    proc._init_thread_local_parsers()
    # The tests only inspect the parsers; close the Bible lookup the setup
    # opened, or Python 3.13 reports the unclosed connection in a later test.
    proc._local.bible_lookup.close()
    return proc._local.streaming_parsers


def test_lithuanian_parser_gets_the_index_when_the_file_exists(bible_db):
    parsers = _parsers(bible_db, str(bible_db))

    assert isinstance(parsers["lt"].verse_text, VerseTextIndex)
    assert parsers["en"].verse_text is None


def test_missing_verse_text_file_leaves_every_parser_without_it(tmp_path, bible_db):
    missing = tmp_path / "bible_ltb.sqlite"

    parsers = _parsers(bible_db, str(missing))

    assert {language: parser.verse_text for language, parser in parsers.items()} == {
        "en": None,
        "lt": None,
    }
    assert not missing.exists()


def test_no_verse_text_path_leaves_every_parser_without_it(bible_db):
    parsers = _parsers(bible_db, None)

    assert all(parser.verse_text is None for parser in parsers.values())
