"""Regression tests for canonical book identity during import.

The October 2026 database corruption came from ``data/kjv.json`` entries whose
``abbrev`` named a different book than their ``name`` (Judges text tagged
``jud``, Jonah tagged ``jn``, John tagged ``jo``, Jude tagged ``jd``). The
importer mapped by abbreviation alone and silently filed each book's verses
under another book's ID. These tests pin the fix: agreeing identifiers import
to the right book, conflicting or unknown identifiers fail loudly.
"""

import json

import pytest

from core.import_bible import BibleImporter, ImportValidationError, resolve_canonical_book


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _importer(tmp_path, name="test.sqlite"):
    importer = BibleImporter(str(tmp_path / name))
    importer.connect()
    return importer


def _import(importer, source):
    importer.create_schema()
    return importer.import_kjv_json(str(source))


class TestResolveCanonicalBook:
    def test_abbrev_only(self):
        assert resolve_canonical_book("jn").name == "John"
        assert resolve_canonical_book("jd").name == "Judges"

    def test_name_only(self):
        assert resolve_canonical_book(name="Jonah").name == "Jonah"
        assert resolve_canonical_book(name="Jude").name == "Jude"

    def test_agreeing_pair(self):
        assert resolve_canonical_book("jd", "Judges").name == "Judges"
        assert resolve_canonical_book("jo", "Jonah").name == "Jonah"
        assert resolve_canonical_book("jn", "John").name == "John"
        assert resolve_canonical_book("jud", "Jude").name == "Jude"

    def test_tolerates_case_and_whitespace(self):
        assert resolve_canonical_book(" JN ", "  john ").name == "John"

    @pytest.mark.parametrize(
        ("abbrev", "name"),
        [
            ("jud", "Judges"),  # exact kjv.json mislabeling
            ("jn", "Jonah"),
            ("jo", "John"),
            ("jd", "Jude"),
        ],
    )
    def test_conflicting_pair_raises(self, abbrev, name):
        with pytest.raises(ImportValidationError, match="Conflicting book identity"):
            resolve_canonical_book(abbrev, name)

    def test_unknown_abbrev_raises(self):
        with pytest.raises(ImportValidationError, match="Unknown book abbreviation"):
            resolve_canonical_book("xyz")

    def test_unknown_name_raises(self):
        with pytest.raises(ImportValidationError, match="Unknown book name"):
            resolve_canonical_book(name="Not A Book")

    def test_missing_identity_raises(self):
        with pytest.raises(ImportValidationError, match="neither"):
            resolve_canonical_book()


def _single_book_source(tmp_path, filename, abbrev, name):
    return _write_json(
        tmp_path / filename,
        [{"abbrev": abbrev, "name": name, "chapters": [["Verse one.", "Verse two."]]}],
    )


class TestListFormatIdentity:
    @pytest.mark.parametrize(
        ("abbrev", "name"),
        [("jd", "Judges"), ("jo", "Jonah"), ("jn", "John"), ("jud", "Jude")],
    )
    def test_correct_pairs_land_under_right_name(self, tmp_path, abbrev, name):
        source = _single_book_source(tmp_path, "book.json", abbrev, name)
        importer = _importer(tmp_path)
        try:
            books, verses = _import(importer, source)
            assert (books, verses) == (1, 2)
            row = importer.conn.execute(
                "SELECT b.name, v.chapter, v.verse, v.text FROM verses v "
                "JOIN books b ON b.id = v.book_id"
            ).fetchall()
            assert all(r[0] == name for r in row)
            assert row[0][3] == "Verse one."
        finally:
            importer.close()

    @pytest.mark.parametrize(
        ("abbrev", "name"),
        [("jud", "Judges"), ("jn", "Jonah"), ("jo", "John"), ("jd", "Jude")],
    )
    def test_mislabeled_pairs_fail_instead_of_misassigning(self, tmp_path, abbrev, name):
        """The original kjv.json corruption must raise, not silently swap books."""
        source = _single_book_source(tmp_path, "bad.json", abbrev, name)
        importer = _importer(tmp_path)
        try:
            with pytest.raises(ImportValidationError, match="Conflicting book identity"):
                _import(importer, source)
        finally:
            importer.close()

    def test_unknown_abbrev_raises(self, tmp_path):
        source = _single_book_source(tmp_path, "unknown.json", "xyz", None)
        importer = _importer(tmp_path)
        try:
            with pytest.raises(ImportValidationError, match="Unknown book abbreviation"):
                _import(importer, source)
        finally:
            importer.close()

    def test_name_only_entry_resolves(self, tmp_path):
        source = _write_json(
            tmp_path / "name_only.json",
            [{"name": "Jude", "chapters": [["Jude, the servant."]]}],
        )
        importer = _importer(tmp_path)
        try:
            assert _import(importer, source) == (1, 1)
            row = importer.conn.execute("SELECT name FROM books").fetchone()
            assert row[0] == "Jude"
        finally:
            importer.close()

    def test_duplicate_book_raises(self, tmp_path):
        source = _write_json(
            tmp_path / "dup.json",
            [
                {"abbrev": "gn", "chapters": [["First."]]},
                {"abbrev": "gn", "chapters": [["Second."]]},
            ],
        )
        importer = _importer(tmp_path)
        try:
            with pytest.raises(ImportValidationError, match="Duplicate book"):
                _import(importer, source)
        finally:
            importer.close()


class TestDictFormatIdentity:
    def test_unknown_abbrev_raises(self, tmp_path):
        source = _write_json(tmp_path / "dict.json", {"xyz": {"1": {"1": "Text."}}})
        importer = _importer(tmp_path)
        try:
            with pytest.raises(ImportValidationError, match="Unknown book abbreviation"):
                _import(importer, source)
        finally:
            importer.close()


def _flat_source(tmp_path, filename, entries):
    return _write_json(tmp_path / filename, {"verses": entries})


class TestFlatFormatIdentity:
    def test_wrong_position_raises(self, tmp_path):
        source = _flat_source(
            tmp_path,
            "pos.json",
            [{"book_name": "John", "book": 44, "chapter": 1, "verse": 1, "text": "Text."}],
        )
        importer = _importer(tmp_path)
        try:
            with pytest.raises(ImportValidationError, match="Conflicting book position"):
                _import(importer, source)
        finally:
            importer.close()

    def test_unknown_book_name_raises(self, tmp_path):
        source = _flat_source(
            tmp_path,
            "name.json",
            [{"book_name": "Not A Book", "book": 1, "chapter": 1, "verse": 1, "text": "T."}],
        )
        importer = _importer(tmp_path)
        try:
            with pytest.raises(ImportValidationError, match="Unknown book name"):
                _import(importer, source)
        finally:
            importer.close()

    def test_order_comes_from_canon_not_source(self, tmp_path):
        source = _flat_source(
            tmp_path,
            "canon.json",
            [
                {
                    "book_name": "John",
                    "book": 43,
                    "chapter": 1,
                    "verse": 1,
                    "text": "In the beginning.",
                }
            ],
        )
        importer = _importer(tmp_path)
        try:
            assert _import(importer, source) == (1, 1)
            row = importer.conn.execute("SELECT name, order_index, testament FROM books").fetchone()
            assert row == ("John", 43, "New Testament")
        finally:
            importer.close()


class TestValidateComplete:
    def test_partial_import_is_incomplete(self, tmp_path):
        source = _single_book_source(tmp_path, "partial.json", "gn", "Genesis")
        importer = _importer(tmp_path)
        try:
            _import(importer, source)
            with pytest.raises(ImportValidationError, match="Incomplete book set"):
                importer.validate_complete()
        finally:
            importer.close()

    @pytest.mark.integration
    def test_full_kjv_source_validates(self, tmp_path):
        """The authoritative ``data/kjv2.json`` must satisfy completeness."""
        from pathlib import Path

        kjv_source = Path(__file__).resolve().parent.parent.parent / "data" / "kjv2.json"
        if not kjv_source.is_file():
            pytest.skip(f"KJV source not found at {kjv_source}")
        importer = BibleImporter(str(tmp_path / "full.sqlite"))
        importer.connect()
        try:
            importer.create_schema()
            books, verses = importer.import_kjv_json(str(kjv_source))
            assert (books, verses) == (66, 31102)
            importer.validate_complete()
            counts = dict(
                importer.conn.execute(
                    "SELECT b.name, COUNT(v.id) FROM books b "
                    "JOIN verses v ON v.book_id = b.id GROUP BY b.id"
                ).fetchall()
            )
            assert counts["John"] == 879
            assert counts["Jonah"] == 48
            assert counts["Judges"] == 618
            assert counts["Jude"] == 25
            text = importer.conn.execute(
                "SELECT v.text FROM verses v JOIN books b ON b.id = v.book_id "
                "WHERE b.name = 'John' AND v.chapter = 3 AND v.verse = 16"
            ).fetchone()[0]
            assert "For God so loved" in text
        finally:
            importer.close()
