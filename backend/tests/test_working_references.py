"""Reference validation against an imported Bible database.

Replaces the old manual "which references work" demo with a deterministic,
parametrized test using a temporary database.
"""

import pytest

from core.bible_lookup import BibleLookup
from core.verse_parser import ReferenceParser

pytestmark = pytest.mark.integration


@pytest.fixture
def parser(bible_db):
    bible = BibleLookup(str(bible_db))
    try:
        yield ReferenceParser(validator=bible.get_validator_hook())
    finally:
        bible.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Genesis 1:1", ["Genesis 1:1"]),
        ("Genesis 1:5", ["Genesis 1:5"]),
        ("Genesis 2:1", ["Genesis 2:1"]),
        ("John 3:16", ["John 3:16"]),
        ("Genesis 1:50", []),  # chapter has 5 verses
        ("Genesis 51:1", []),  # book has 2 chapters
        ("John 3:18", []),  # chapter has 17 verses
        ("UnknownBook 1:1", []),
    ],
)
def test_working_refs(parser, text, expected):
    refs = [str(r) for r in parser.parse(text)]
    assert refs == expected


def test_context_inference(parser):
    """Chapter context carries over to a bare verse mention (streaming)."""
    from core.verse_parser import StreamingParser

    stream = StreamingParser(parser)
    assert [str(r) for r in stream.feed("Genesis 1")] == ["Genesis 1"]
    refs = stream.feed("look at verse 5")
    assert [str(r) for r in refs] == ["Genesis 1:5"]
