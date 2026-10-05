"""Tests for the shared 66-book canon (``core/books.py``).

P1-3: the parser, importer, and STT correction regex all derive their book data
from ``CANONICAL_BOOKS`` so the copies cannot drift (the ``timon``/``timothy``
bug came from maintaining them separately).
"""

from core.books import CANONICAL_BOOKS
from core.import_bible import ABBREV_TO_NAME, BOOK_ORDER
from core.verse_parser import BOOKS, CHAPTERS


class TestCanonIntegrity:
    def test_66_books_1189_chapters(self):
        assert len(CANONICAL_BOOKS) == 66
        assert sum(book.chapters for book in CANONICAL_BOOKS) == 1189

    def test_orders_are_contiguous_one_to_66(self):
        assert [book.order for book in CANONICAL_BOOKS] == list(range(1, 67))

    def test_abbreviations_are_unique(self):
        abbrevs = [book.abbrev for book in CANONICAL_BOOKS]
        assert len(abbrevs) == 66
        assert len(set(abbrevs)) == 66


class TestDerivedTablesMatchCanon:
    """The parser and importer tables must be derived, not separate copies."""

    def test_parser_books_match_canon(self):
        expected_books = [(b.name, b.chapters, list(b.variants)) for b in CANONICAL_BOOKS]
        assert expected_books == BOOKS
        expected_chapters = {b.name: b.chapters for b in CANONICAL_BOOKS}
        assert expected_chapters == CHAPTERS

    def test_importer_order_matches_canon(self):
        expected_order = {b.name: b.order for b in CANONICAL_BOOKS}
        assert expected_order == BOOK_ORDER

    def test_importer_abbrev_map_matches_canon(self):
        expected_abbrev = {b.abbrev: b.name for b in CANONICAL_BOOKS}
        assert expected_abbrev == ABBREV_TO_NAME
