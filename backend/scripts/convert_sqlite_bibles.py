#!/usr/bin/env python3
"""Convert third-party Bible SQLite files into the BibleLive schema.

Two source layouts are supported (auto-detected, override with ``--format``):

* ``web`` — a bare ``verses(book_id, chapter, number, text)`` table where
  ``book_id`` is the 1-based canonical order and the verse column is called
  ``number`` (e.g. ``bible_data/web.sqlite``). There is no ``books`` table.
* ``mybible`` — MyBible-format files with ``books(book_number, short_name,
  long_name, ...)`` and ``verses(book_number, chapter, verse, text)`` where
  ``book_number`` is 10, 20, ..., 730 (e.g. ``bible_data/LTB.SQLite3``).

Output matches the importer schema (``books`` / ``verses`` / ``bible_metadata``,
see ``DATABASE_SETUP.md``) so the result works with ``BibleLookup`` unchanged.
Book identity always comes from the shared canon (``core/books.py``): the
``books.name`` column carries the English canonical name. MyBible sources also
keep their own book title in an additive ``books.local_name`` column (NULL for
``web`` sources) for the future translated-UI work; nothing reads it yet.

Usage:
    python backend/scripts/convert_sqlite_bibles.py \\
        --source bible_data/web.sqlite --translation web \\
        --output bible_data/bible_web.sqlite
    python backend/scripts/convert_sqlite_bibles.py \\
        --source bible_data/LTB.SQLite3 --translation ltb \\
        --output bible_data/bible_ltb.sqlite
"""

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bible_lookup import clean_verse_text
from core.books import CANONICAL_BOOKS, Book
from core.import_bible import BibleImporter, ImportValidationError

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ORDER_TO_BOOK: dict[int, Book] = {book.order: book for book in CANONICAL_BOOKS}


def detect_format(src: sqlite3.Connection) -> str:
    """Return ``'web'`` or ``'mybible'`` based on the source tables/columns."""
    tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "books" in tables and "verses" in tables:
        verse_cols = {row[1] for row in src.execute("PRAGMA table_info(verses)")}
        if "book_number" in verse_cols:
            return "mybible"
    if "verses" in tables:
        verse_cols = {row[1] for row in src.execute("PRAGMA table_info(verses)")}
        if {"book_id", "chapter", "number", "text"} <= verse_cols:
            return "web"
    raise ImportValidationError(
        f"Unrecognized source schema (tables: {sorted(tables)}). "
        "Expected the 'web' verses-only layout or the MyBible books/verses layout."
    )


def _insert_canon_books(
    importer: BibleImporter, local_names: dict[int, str] | None = None
) -> dict[int, int]:
    """Insert the 66 canonical books in order; return {canon_order: books.id}."""
    if importer.conn is None:
        raise RuntimeError("Database not connected")
    cursor = importer.conn.cursor()
    order_to_id: dict[int, int] = {}
    for book in CANONICAL_BOOKS:
        testament = "Old Testament" if book.order <= 39 else "New Testament"
        cursor.execute(
            "INSERT INTO books (name, testament, order_index, local_name) VALUES (?, ?, ?, ?)",
            (book.name, testament, book.order, (local_names or {}).get(book.order)),
        )
        order_to_id[book.order] = cursor.lastrowid
    importer.conn.commit()
    return order_to_id


def convert_web(src: sqlite3.Connection, importer: BibleImporter) -> tuple[int, int]:
    """Convert the bare ``verses(book_id, chapter, number, text)`` layout."""
    rows = src.execute("SELECT book_id, chapter, number, text FROM verses ORDER BY id").fetchall()
    orders = sorted({row[0] for row in rows})
    if orders != list(range(1, 67)):
        raise ImportValidationError(
            f"WEB book_id values are not the canonical 1-66 range: {orders[:5]}...{orders[-3:]}"
        )
    order_to_id = _insert_canon_books(importer)
    if importer.conn is None:
        raise RuntimeError("Database not connected")
    cursor = importer.conn.cursor()
    verses_imported = 0
    skipped = 0
    for book_id, chapter, number, text in rows:
        cleaned = clean_verse_text(text or "")
        if not cleaned:
            skipped += 1
            continue
        cursor.execute(
            "INSERT OR REPLACE INTO verses (book_id, chapter, verse, text) VALUES (?, ?, ?, ?)",
            (order_to_id[book_id], chapter, number, cleaned),
        )
        verses_imported += 1
    importer.conn.commit()
    if skipped:
        logger.warning(f"Skipped {skipped} verses with empty text after cleaning")
    logger.info(f"Imported {len(order_to_id)} books, {verses_imported} verses (web layout)")
    return len(order_to_id), verses_imported


def convert_mybible(src: sqlite3.Connection, importer: BibleImporter) -> tuple[int, int]:
    """Convert the MyBible ``books``/``verses`` layout keyed by book_number.

    MyBible book numbers leave gaps for deuterocanonical books (e.g. Esther is
    190, Job 220), so books are mapped onto the canon by ascending book_number
    (which matches ``sorting_order``), not by arithmetic. The mapping is
    cross-checked against the canon chapter counts: every book's max chapter
    must equal the canon's, otherwise books would land on the wrong names.
    """
    ordered_numbers = [
        row[0] for row in src.execute("SELECT book_number FROM books ORDER BY book_number")
    ]
    if len(ordered_numbers) != len(CANONICAL_BOOKS):
        raise ImportValidationError(
            f"MyBible source has {len(ordered_numbers)} books, expected {len(CANONICAL_BOOKS)}"
        )
    long_names = {row[0]: row[1] for row in src.execute("SELECT book_number, long_name FROM books")}
    local_names: dict[int, str] = {}
    for order, (book_number, canon) in enumerate(
        zip(ordered_numbers, CANONICAL_BOOKS, strict=True), 1
    ):
        max_chapter = src.execute(
            "SELECT MAX(chapter) FROM verses WHERE book_number = ?", (book_number,)
        ).fetchone()[0]
        if max_chapter != canon.chapters:
            raise ImportValidationError(
                f"Book #{book_number} has {max_chapter} chapters, "
                f"canon expects {canon.chapters} for {canon.name!r}: "
                "book order does not match the canon"
            )
        local_names[order] = long_names[book_number]
    order_to_id = _insert_canon_books(importer, local_names)
    number_to_order = {n: order for order, n in enumerate(ordered_numbers, 1)}
    if importer.conn is None:
        raise RuntimeError("Database not connected")
    cursor = importer.conn.cursor()
    batch: list[tuple[int, int, int, str]] = []
    verses_imported = 0
    skipped = 0

    def flush() -> None:
        cursor.executemany(
            "INSERT OR REPLACE INTO verses (book_id, chapter, verse, text) VALUES (?, ?, ?, ?)",
            batch,
        )
        batch.clear()

    for book_number, chapter, verse, text in src.execute(
        "SELECT book_number, chapter, verse, text FROM verses"
    ):
        order = number_to_order.get(book_number)
        if order is None:
            raise ImportValidationError(f"Verse references unknown book_number: {book_number!r}")
        cleaned = clean_verse_text(text or "")
        if not cleaned:
            skipped += 1
            continue
        batch.append((order_to_id[order], chapter, verse, cleaned))
        verses_imported += 1
        if len(batch) >= 2000:
            flush()
    flush()
    importer.conn.commit()
    if skipped:
        logger.warning(f"Skipped {skipped} verses with empty text after cleaning")
    logger.info(f"Imported {len(order_to_id)} books, {verses_imported} verses (mybible layout)")
    return len(order_to_id), verses_imported


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a third-party Bible SQLite file into the BibleLive schema"
    )
    parser.add_argument("--source", required=True, help="Existing third-party SQLite file")
    parser.add_argument(
        "--translation", required=True, help="Translation id for metadata (e.g. 'web', 'ltb')"
    )
    parser.add_argument("--output", required=True, help="New BibleLive-schema database path")
    parser.add_argument(
        "--format",
        choices=("auto", "web", "mybible"),
        default="auto",
        help="Source layout (default: detect from schema)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite the output file if it exists"
    )
    args = parser.parse_args(argv)

    source = Path(args.source)
    output = Path(args.output)
    if not source.is_file():
        logger.error(f"Source not found: {source}")
        return 1
    if output.exists() and not args.force:
        logger.error(f"Output already exists: {output} (use --force to overwrite)")
        return 1

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        source_format = args.format if args.format != "auto" else detect_format(src)
        logger.info(f"Source layout: {source_format} ({source})")

        # Build into a temp file and promote only after validation, mirroring
        # the importer so a failure never leaves a partial output behind.
        staging = output.with_name(f"{output.name}.tmp-{os.getpid()}")
        if staging.exists():
            staging.unlink()
        importer = BibleImporter(str(staging))
        try:
            importer.connect()
            importer.create_schema()
            if importer.conn is not None:
                importer.conn.execute("ALTER TABLE books ADD COLUMN local_name TEXT")
            if source_format == "web":
                books, verses = convert_web(src, importer)
            else:
                books, verses = convert_mybible(src, importer)
            importer.validate_complete()
            importer.set_metadata(args.translation, str(source))
            importer.close()
            os.replace(staging, output)
        finally:
            importer.close()
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                logger.warning(f"Could not remove staging database: {staging}")
    except ImportValidationError as e:
        logger.error(f"Conversion failed: {e}")
        return 1
    finally:
        src.close()

    print("\n" + "=" * 60)
    print("Conversion complete!")
    print("=" * 60)
    print(f"Database:    {output}")
    print(f"Translation: {args.translation}")
    print(f"Books:       {books}")
    print(f"Verses:      {verses}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
