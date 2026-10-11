#!/usr/bin/env python3
"""
Bible Import CLI

Explicit one-time setup command to import Bible translations into SQLite database.
Separates data import from runtime operation.

Usage:
    python -m core.import_bible --translation kjv --source data/kjv2.json --output bible_data/kjv.sqlite
    python -m core.import_bible --translation kjv --source data/kjv2.json  # Uses default output path
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from .bible_lookup import clean_verse_text
from .books import CANONICAL_BOOKS, Book
from .config import config

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Canonical 66-book order; index 1-39 is the Old Testament, 40-66 the New.
# Both maps are derived from the shared canon so they cannot drift from the
# parser (see core/books.py).
BOOK_ORDER = {book.name: book.order for book in CANONICAL_BOOKS}
ABBREV_TO_NAME = {book.abbrev: book.name for book in CANONICAL_BOOKS}
NAME_TO_BOOK = {book.name.casefold(): book for book in CANONICAL_BOOKS}


class ImportValidationError(ValueError):
    """A source file cannot be mapped onto the canonical 66-book canon."""


def _tolerate_console_encoding() -> None:
    """Degrade unencodable console characters instead of crashing the CLI.

    Windows consoles historically use cp1252, which cannot encode the check
    marks in importer output; without this, a completed import exits 1 from a
    ``UnicodeEncodeError`` raised while printing the summary. Only this
    process's console error handling is touched — no encoding, data, or
    application behavior changes.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            continue


def _normalize_abbrev(value: object) -> str:
    return str(value).strip().lower() if value is not None else ""


def _normalize_name(value: object) -> str:
    return " ".join(str(value).split()).casefold() if value is not None else ""


def resolve_canonical_book(abbrev: object = None, name: object = None) -> Book:
    """Resolve source book identity to a canonical :class:`Book`.

    Both the source abbreviation and the source book name are checked against
    ``core/books.py``. When both are present they must agree; a conflict means
    the source row is mislabeled (this is how Judges/Jude and John/Jonah texts
    were once assigned to each other's canonical IDs) and the import is
    rejected instead of guessing.

    Raises:
        ImportValidationError: If neither identifier is known, or they map to
            different canonical books.
    """
    norm_abbrev = _normalize_abbrev(abbrev)
    norm_name = _normalize_name(name)
    book_by_abbrev = (
        NAME_TO_BOOK.get(ABBREV_TO_NAME[norm_abbrev].casefold())
        if norm_abbrev in ABBREV_TO_NAME
        else None
    )
    book_by_name = NAME_TO_BOOK.get(norm_name) if norm_name else None

    if book_by_abbrev is not None:
        if book_by_name is not None and book_by_name != book_by_abbrev:
            raise ImportValidationError(
                f"Conflicting book identity: abbreviation {abbrev!r} maps to "
                f"{book_by_abbrev.name!r} but name {name!r} maps to {book_by_name.name!r}"
            )
        return book_by_abbrev
    if book_by_name is not None:
        return book_by_name
    if norm_abbrev:
        raise ImportValidationError(f"Unknown book abbreviation: {abbrev!r}")
    if norm_name:
        raise ImportValidationError(f"Unknown book name: {name!r}")
    raise ImportValidationError("Source entry has neither a book abbreviation nor a name")


class BibleImporter:
    """Handles one-time import of Bible text into SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.conn: sqlite3.Connection | None = None

    def create_schema(self) -> None:
        """Create database schema with metadata table."""
        if self.conn is None:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()

        # Books table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                testament TEXT,
                order_index INTEGER
            )
        """)

        # Verses table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS verses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                chapter INTEGER NOT NULL,
                verse INTEGER NOT NULL,
                text TEXT NOT NULL,
                FOREIGN KEY (book_id) REFERENCES books(id),
                UNIQUE(book_id, chapter, verse)
            )
        """)

        # Metadata table - tracks import information
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bible_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Migrations: CREATE TABLE IF NOT EXISTS leaves older databases untouched,
        # so add any columns introduced after the table was first created.
        self._add_missing_columns(cursor, "books", {"testament": "TEXT", "order_index": "INTEGER"})

        # Create indexes for fast lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_verses_lookup
            ON verses(book_id, chapter, verse)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_books_name
            ON books(name)
        """)

        self.conn.commit()
        logger.info("✓ Database schema created")

    def _drop_content_tables(self) -> None:
        """Drop and recreate ``books``/``verses`` with the current schema.

        Older databases carry a ``books`` table with a ``chapter_count`` column
        declared ``NOT NULL`` and no default, which makes inserts fail unless the
        column is supplied. Recreating the tables keeps ``CREATE TABLE IF NOT
        EXISTS`` from silently keeping an incompatible definition, and guarantees
        a re-import contains only rows from the new source.
        """
        if self.conn is None:
            raise RuntimeError("Database not connected")
        cursor = self.conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS verses")
        cursor.execute("DROP TABLE IF EXISTS books")
        self.create_schema()

    @staticmethod
    def _add_missing_columns(cursor: sqlite3.Cursor, table: str, columns: dict[str, str]) -> None:
        """ALTER TABLE to add columns that are missing from an existing table.

        Keeps databases created by an older schema usable without a full
        re-import. Only additive changes are applied; existing data is preserved.
        """
        existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
        if not existing:
            return  # Table does not exist yet; CREATE TABLE handles it
        for column, column_type in columns.items():
            if column not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                logger.info(f"✓ Migrated {table}.{column} column added")

    def set_metadata(self, translation: str, source_file: str, version: str = "1.0") -> None:
        """Set metadata about this Bible database."""
        if self.conn is None:
            raise RuntimeError("Database not connected")

        metadata = {
            "translation": translation,
            "source_file": Path(source_file).name,
            "import_timestamp": datetime.now(UTC).isoformat(),
            "version": version,
        }

        cursor = self.conn.cursor()
        for key, value in metadata.items():
            cursor.execute(
                "INSERT OR REPLACE INTO bible_metadata (key, value) VALUES (?, ?)", (key, value)
            )
        self.conn.commit()
        logger.info(f"✓ Metadata set: {translation} from {Path(source_file).name}")

    def get_metadata(self) -> dict:
        """Get metadata from existing database."""
        if self.conn is None:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        rows = cursor.execute("SELECT key, value FROM bible_metadata").fetchall()
        return dict(rows)

    def validate_complete(self) -> None:
        """Require the full canonical 66-book canon in the connected database.

        Every canonical book must be present exactly once and hold at least one
        verse. Raises :class:`ImportValidationError` otherwise. The CLI runs
        this before promoting an import so a source with missing, extra, or
        empty books — or with mislabeled book identity — can never become the
        output database. This is a book-level check only: it does not verify
        per-book chapter or verse counts, so chapter- or verse-level truncation
        within a present book is not detected (possible future work).
        """
        if self.conn is None:
            raise RuntimeError("Database not connected")

        names = [row[0] for row in self.conn.execute("SELECT name FROM books")]
        expected = [book.name for book in CANONICAL_BOOKS]
        missing = [name for name in expected if name not in names]
        extra = [name for name in names if name not in expected]
        if missing or extra or len(names) != len(expected):
            raise ImportValidationError(
                f"Incomplete book set: missing={missing} extra={extra} books={len(names)}"
            )
        empty = [
            row[0]
            for row in self.conn.execute(
                "SELECT b.name FROM books b LEFT JOIN verses v ON v.book_id = b.id "
                "GROUP BY b.id HAVING COUNT(v.id) = 0"
            )
        ]
        if empty:
            raise ImportValidationError(f"Books without verses: {empty}")

    def import_kjv_json(self, json_path: str) -> tuple[int, int]:
        """Import Bible text from JSON.

        Supports three on-disk formats:
          1. Flat e-Sword list: ``{"verses": [{"book_name", "book", "chapter",
             "verse", "text"}, ...]}`` (used by ``data/kjv2.json``)
          2. List format: ``[{"abbrev": "gn", "chapters": [[verse, ...], ...]}, ...]``
          3. Dict format (legacy): ``{"gn": {"1": {"1": "text"}}, ...}``

        Verse text is cleaned with :func:`core.bible_lookup.clean_verse_text`,
        so editorial markup never reaches the display.

        Files are read as ``utf-8-sig`` to tolerate a byte-order mark.

        Every source entry is resolved to a canonical book with
        :func:`resolve_canonical_book`: entries whose abbreviation and name
        disagree, or that name an unknown book, raise
        :class:`ImportValidationError` instead of silently misassigning
        verses. Call :meth:`validate_complete` afterwards (the CLI does) to
        require all 66 canonical books, each with at least one verse, before
        promoting the output. That check is book-level: it does not verify
        chapter or verse counts within a book.

        Returns:
            Tuple of (books_imported, verses_imported)
        """
        if self.conn is None:
            raise RuntimeError("Database not connected")

        json_file = Path(json_path)
        if not json_file.exists():
            raise FileNotFoundError(f"JSON file not found: {json_path}")

        logger.info(f"Reading {json_path}...")
        with open(json_file, encoding="utf-8-sig") as f:
            data = json.load(f)

        if isinstance(data, dict) and isinstance(data.get("verses"), list):
            # Flat format carries full book names, so no abbrev mapping needed.
            return self._import_flat_verses(data["verses"])

        # Normalize the remaining formats to {canonical abbrev: {chapter: [verse texts]}},
        # resolving every entry against the canon up front so a mislabeled
        # source fails before any row is written.
        seen_books: set[str] = set()
        if isinstance(data, list):
            normalized: dict[str, dict[int, list]] = {}
            for entry in data:
                if not isinstance(entry, dict):
                    logger.warning(f"Skipping malformed book entry: {entry!r}")
                    continue
                chapters = entry.get("chapters")
                if not isinstance(chapters, list):
                    logger.warning(
                        "Skipping book entry without chapters: "
                        f"{entry.get('abbrev') or entry.get('name')!r}"
                    )
                    continue
                book = resolve_canonical_book(entry.get("abbrev"), entry.get("name"))
                if book.name in seen_books:
                    raise ImportValidationError(f"Duplicate book in source: {book.name!r}")
                seen_books.add(book.name)
                normalized[book.abbrev] = {
                    index + 1: verses for index, verses in enumerate(chapters)
                }
        else:
            if not isinstance(data, dict):
                raise ImportValidationError(f"Unsupported source JSON shape: {type(data).__name__}")
            normalized = {}
            for abbrev, chapters in data.items():
                book = resolve_canonical_book(abbrev)
                if not isinstance(chapters, dict):
                    raise ImportValidationError(
                        f"Book {book.name!r} has no chapter mapping in source"
                    )
                if book.name in seen_books:
                    raise ImportValidationError(f"Duplicate book in source: {book.name!r}")
                seen_books.add(book.name)
                normalized[book.abbrev] = {
                    int(chapter): list(verses.values()) for chapter, verses in chapters.items()
                }

        # Rebuild content tables so a re-import reflects only this source.
        self._drop_content_tables()
        cursor = self.conn.cursor()
        books_imported = 0
        verses_imported = 0

        logger.info("Importing books and verses...")

        for abbrev, chapters in normalized.items():
            # Keys are canonical abbreviations (see normalization above), so the
            # book row always carries the canon's name, order, and testament.
            book = NAME_TO_BOOK[ABBREV_TO_NAME[abbrev].casefold()]
            testament = "Old Testament" if book.order <= 39 else "New Testament"

            # Insert book
            cursor.execute(
                "INSERT OR IGNORE INTO books (name, testament, order_index) VALUES (?, ?, ?)",
                (book.name, testament, book.order),
            )
            book_id = cursor.execute(
                "SELECT id FROM books WHERE name = ?", (book.name,)
            ).fetchone()[0]
            books_imported += 1

            # Insert verses
            for chapter, verse_texts in chapters.items():
                for verse, text in enumerate(verse_texts, 1):
                    if not text:
                        continue
                    cursor.execute(
                        "INSERT OR REPLACE INTO verses (book_id, chapter, verse, text) VALUES (?, ?, ?, ?)",
                        (book_id, chapter, verse, clean_verse_text(text)),
                    )
                    verses_imported += 1

        self.conn.commit()
        logger.info(f"✓ Imported {books_imported} books, {verses_imported} verses")

        return books_imported, verses_imported

    def _import_flat_verses(self, verses: list) -> tuple[int, int]:
        """Import the flat e-Sword ``[{book_name, chapter, verse, text}]`` format.

        Book names are resolved against the canon (:func:`resolve_canonical_book`);
        an unknown name, or a ``book`` position that disagrees with the canonical
        order, raises :class:`ImportValidationError` instead of misassigning rows.
        ``order_index`` and testament always come from the canon, never from the
        source's position field.
        """
        if self.conn is None:
            raise RuntimeError("Database not connected")

        cursor = self.conn.cursor()
        books_imported = 0
        verses_imported = 0
        seen_books: set[str] = set()

        # Re-import into an existing database must not keep verses from the
        # previous source (a truncated source would otherwise survive).
        self._drop_content_tables()
        cursor = self.conn.cursor()

        logger.info("Importing books and verses (flat format)...")

        for entry in verses:
            if not isinstance(entry, dict):
                logger.warning(f"Skipping malformed verse entry: {entry!r}")
                continue

            book_name = entry.get("book_name")
            try:
                chapter = int(entry["chapter"])
                verse = int(entry["verse"])
            except (KeyError, TypeError, ValueError):
                logger.warning(f"Skipping verse with bad coordinates: {entry!r}")
                continue

            if not book_name:
                logger.warning(f"Skipping verse without book name: {entry!r}")
                continue

            book = NAME_TO_BOOK.get(_normalize_name(book_name))
            if book is None:
                raise ImportValidationError(f"Unknown book name: {book_name!r}")
            position = entry.get("book")
            if (
                isinstance(position, int)
                and not isinstance(position, bool)
                and position != book.order
            ):
                raise ImportValidationError(
                    f"Conflicting book position: {book_name!r} is book {position} in "
                    f"source but book {book.order} in the canon"
                )

            text = clean_verse_text(entry.get("text") or "")
            if not text:
                continue

            if book.name not in seen_books:
                books_imported += 1
                seen_books.add(book.name)
                testament = "Old Testament" if book.order <= 39 else "New Testament"
                cursor.execute(
                    "INSERT OR IGNORE INTO books (name, testament, order_index) VALUES (?, ?, ?)",
                    (book.name, testament, book.order),
                )

            book_row = cursor.execute(
                "SELECT id FROM books WHERE name = ?", (book.name,)
            ).fetchone()
            if not book_row:
                continue

            cursor.execute(
                "INSERT OR REPLACE INTO verses (book_id, chapter, verse, text) VALUES (?, ?, ?, ?)",
                (book_row[0], chapter, verse, text),
            )
            verses_imported += 1

        self.conn.commit()
        logger.info(f"✓ Imported {books_imported} books, {verses_imported} verses")

        return books_imported, verses_imported

    def connect(self) -> None:
        """Connect to database (create if needed)."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        logger.info(f"✓ Connected to {self.db_path}")

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("✓ Database closed")

    def show_info(self) -> None:
        """Display information about the database."""
        if self.conn is None:
            raise RuntimeError("Database not connected")

        metadata = self.get_metadata()
        cursor = self.conn.cursor()

        book_count = cursor.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        verse_count = cursor.execute("SELECT COUNT(*) FROM verses").fetchone()[0]

        print("\nDatabase Information:")
        print("=" * 60)
        print(f"Translation:      {metadata.get('translation', 'Unknown')}")
        print(f"Source:           {metadata.get('source_file', 'Unknown')}")
        print(f"Import Date:      {metadata.get('import_timestamp', 'Unknown')}")
        print(f"Version:          {metadata.get('version', 'Unknown')}")
        print(f"Books:            {book_count}")
        print(f"Verses:           {verse_count}")
        print(f"Database Size:    {self.db_path.stat().st_size / 1024 / 1024:.2f} MB")
        print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Bible translation into SQLite database")
    parser.add_argument(
        "--translation", default="kjv", help="Translation identifier (e.g., 'kjv', 'niv')"
    )
    parser.add_argument(
        "--source",
        required=False,
        default=None,
        help="Path to source JSON file (required for import; not used with --info)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output database path (default: the configured project database). "
            "An explicit relative path is resolved against the current working "
            "directory."
        ),
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Show database info and exit (no import; --source not needed)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Force reimport even if database exists"
    )

    args = parser.parse_args(argv)
    # The default output is the configured project database (resolved against
    # the project root), so running the CLI from another directory cannot
    # create a second database next to the caller's working directory. An
    # explicitly passed relative path keeps the usual working-directory base.
    output = config.resolve_db_path() if args.output is None else Path(args.output)
    _tolerate_console_encoding()

    # Info mode only inspects the existing database; it never writes.
    if args.info:
        if not output.exists():
            logger.error(f"Database not found: {output}")
            return 1
        importer = BibleImporter(str(output))
        try:
            importer.connect()
            importer.show_info()
            return 0
        except Exception as e:
            logger.error(f"Could not read database info: {e}")
            return 1
        finally:
            importer.close()

    if not args.source:
        parser.error("--source is required for import (not needed with --info)")

    # Refusing a re-import must not create anything: probe the existing file
    # read-only instead of connecting first.
    if output.exists() and not args.force:
        try:
            probe = sqlite3.connect(f"file:{output}?mode=ro", uri=True)
            try:
                metadata = dict(probe.execute("SELECT key, value FROM bible_metadata").fetchall())
            except sqlite3.OperationalError:
                metadata = {}
            finally:
                probe.close()
        except sqlite3.Error:
            metadata = {}
        if metadata.get("translation"):
            logger.warning(
                f"Database already exists with translation: {metadata.get('translation')}"
            )
            logger.warning("Use --force to reimport or --info to view details")
            return 1

    # Build into a temporary file on the same volume and promote only after the
    # import validates, so a failure can never leave a partial output behind.
    staging = output.with_name(f"{output.name}.tmp-{os.getpid()}")
    if staging.exists():
        staging.unlink()
    importer = BibleImporter(str(staging))

    try:
        logger.info(f"Importing {args.translation} from {args.source}...")
        importer.connect()
        importer.create_schema()

        books, verses = importer.import_kjv_json(args.source)
        importer.validate_complete()
        importer.set_metadata(args.translation, args.source)
        importer.close()

        os.replace(staging, output)

        print("\n" + "=" * 60)
        print("✓ Import Complete!")
        print("=" * 60)
        print(f"Database:    {output}")
        print(f"Translation: {args.translation}")
        print(f"Books:       {books}")
        print(f"Verses:      {verses}")
        print("=" * 60)

        return 0

    except Exception as e:
        logger.error(f"Import failed: {e}")
        return 1
    finally:
        importer.close()
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            logger.warning(f"Could not remove staging database: {staging}")


if __name__ == "__main__":
    sys.exit(main())
