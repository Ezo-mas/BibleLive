"""Tests for importing the flat e-Sword JSON format (``data/kjv2.json``).

``data/kjv2.json`` carries the complete 31102-verse text in a flat list format
that the importer must understand. (The older ``data/kjv.json`` is also
complete at 31100 verses but mislabels four books — Judges/Jude and John/Jonah
carry each other's abbreviations — so it cannot be imported as-is; see
``test_import_book_identity.py``.)
"""

import json

import pytest

from core.import_bible import BibleImporter

pytestmark = pytest.mark.integration


def _write_flat(path, verses):
    path.write_text(json.dumps({"metadata": {}, "verses": verses}), encoding="utf-8")
    return path


def _import(importer, source):
    importer.create_schema()
    return importer.import_kjv_json(str(source))


def test_imports_flat_verse_list(tmp_path):
    source = _write_flat(
        tmp_path / "flat.json",
        [
            {
                "book_name": "John",
                "book": 43,
                "chapter": 3,
                "verse": 16,
                "text": "¶ For God so {loved} the world.",
            },
            {
                "book_name": "John",
                "book": 43,
                "chapter": 3,
                "verse": 17,
                "text": "For God sent not his Son.",
            },
        ],
    )

    importer = BibleImporter(str(tmp_path / "flat.sqlite"))
    importer.connect()
    try:
        books, verses = _import(importer, source)
        assert books == 1
        assert verses == 2
        # Supplied words keep their text; the pilcrow is dropped.
        stored = importer.conn.execute("SELECT text FROM verses WHERE verse = 16").fetchone()
        assert stored[0] == "For God so loved the world."
    finally:
        importer.close()


def test_assigns_testament_by_canonical_position(tmp_path):
    source = _write_flat(
        tmp_path / "testament.json",
        [
            {
                "book_name": "Genesis",
                "book": 1,
                "chapter": 1,
                "verse": 1,
                "text": "In the beginning.",
            },
            {
                "book_name": "Matthew",
                "book": 40,
                "chapter": 1,
                "verse": 1,
                "text": "The book of the generation.",
            },
        ],
    )

    importer = BibleImporter(str(tmp_path / "testament.sqlite"))
    importer.connect()
    try:
        _import(importer, source)
        rows = dict(importer.conn.execute("SELECT name, testament FROM books"))
        assert rows["Genesis"] == "Old Testament"
        assert rows["Matthew"] == "New Testament"
    finally:
        importer.close()


def test_reimport_replaces_previous_rows(tmp_path):
    first = _write_flat(
        tmp_path / "first.json",
        [{"book_name": "Genesis", "book": 1, "chapter": 1, "verse": 1, "text": "First verse."}],
    )
    second = _write_flat(
        tmp_path / "second.json",
        [
            {"book_name": "Genesis", "book": 1, "chapter": 1, "verse": 1, "text": "Second verse."},
            {"book_name": "Genesis", "book": 1, "chapter": 1, "verse": 2, "text": "Another verse."},
        ],
    )

    importer = BibleImporter(str(tmp_path / "twice.sqlite"))
    importer.connect()
    try:
        _import(importer, first)
        _, imported = _import(importer, second)
        assert imported == 2
        rows = importer.conn.execute("SELECT verse, text FROM verses ORDER BY verse").fetchall()
        assert rows == [(1, "Second verse."), (2, "Another verse.")]
    finally:
        importer.close()


def test_skips_malformed_entries(tmp_path):
    source = _write_flat(
        tmp_path / "bad.json",
        [
            {"book_name": "Genesis", "chapter": 1, "verse": 1, "text": "In the beginning."},
            {"book_name": "Genesis", "chapter": 1, "text": "no verse number"},
            {"chapter": 1, "verse": 2, "text": "no book name"},
            {"book_name": "Genesis", "chapter": "x", "verse": 3, "text": "bad chapter"},
            "not a dict",
        ],
    )

    importer = BibleImporter(str(tmp_path / "bad.sqlite"))
    importer.connect()
    try:
        books, verses = _import(importer, source)
        assert (books, verses) == (1, 1)
    finally:
        importer.close()


def test_skips_empty_verses(tmp_path):
    source = _write_flat(
        tmp_path / "empty.json",
        [
            {
                "book_name": "Genesis",
                "book": 1,
                "chapter": 1,
                "verse": 1,
                "text": "In the beginning.",
            },
            {"book_name": "Genesis", "book": 1, "chapter": 1, "verse": 2, "text": "   "},
        ],
    )

    importer = BibleImporter(str(tmp_path / "empty.sqlite"))
    importer.connect()
    try:
        books, verses = _import(importer, source)
        assert (books, verses) == (1, 1)
    finally:
        importer.close()
