#!/usr/bin/env python3
"""
Bible Import CLI

Explicit one-time setup command to import Bible translations into SQLite database.
Separates data import from runtime operation.

Usage:
    python -m core.import_bible --translation kjv --source data/kjv.json --output bible_data/kjv.sqlite
    python -m core.import_bible --translation kjv --source data/kjv.json  # Uses default output path
"""

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class BibleImporter:
    """Handles one-time import of Bible text into SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.conn: Optional[sqlite3.Connection] = None

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

    def set_metadata(self, translation: str, source_file: str, version: str = "1.0") -> None:
        """Set metadata about this Bible database."""
        if self.conn is None:
            raise RuntimeError("Database not connected")

        metadata = {
            "translation": translation,
            "source_file": Path(source_file).name,
            "import_timestamp": datetime.now(timezone.utc).isoformat(),
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

    def import_kjv_json(self, json_path: str) -> tuple[int, int]:
        """Import KJV Bible from JSON format.

        Supports two on-disk formats:
          1. List format: ``[{"abbrev": "gn", "chapters": [[verse, ...], ...]}, ...]``
          2. Dict format (legacy): ``{"gn": {"1": {"1": "text"}}, ...}``

        Files are read as ``utf-8-sig`` to tolerate a byte-order mark.

        Returns:
            Tuple of (books_imported, verses_imported)
        """
        if self.conn is None:
            raise RuntimeError("Database not connected")

        json_file = Path(json_path)
        if not json_file.exists():
            raise FileNotFoundError(f"JSON file not found: {json_path}")

        logger.info(f"Reading {json_path}...")
        with open(json_path, encoding="utf-8-sig") as f:
            data = json.load(f)

        # Normalize both formats to {abbrev: {chapter_number: [verse texts]}}
        if isinstance(data, list):
            normalized: dict[str, dict[int, list]] = {}
            for entry in data:
                if not isinstance(entry, dict):
                    logger.warning(f"Skipping malformed book entry: {entry!r}")
                    continue
                abbrev = str(entry.get("abbrev") or entry.get("name") or "").lower()
                chapters = entry.get("chapters")
                if not abbrev or not isinstance(chapters, list):
                    logger.warning(f"Skipping malformed book entry for {abbrev!r}")
                    continue
                normalized[abbrev] = {index + 1: verses for index, verses in enumerate(chapters)}
        else:
            normalized = {
                str(abbrev).lower(): {
                    int(chapter): list(verses.values()) for chapter, verses in chapters.items()
                }
                for abbrev, chapters in data.items()
            }

        # Mapping from KJV JSON abbreviations to full book names
        abbrev_to_name = {
            "gn": "Genesis",
            "ex": "Exodus",
            "lv": "Leviticus",
            "nm": "Numbers",
            "dt": "Deuteronomy",
            "js": "Joshua",
            "jd": "Judges",
            "rt": "Ruth",
            "1sm": "1 Samuel",
            "2sm": "2 Samuel",
            "1kgs": "1 Kings",
            "2kgs": "2 Kings",
            "1ch": "1 Chronicles",
            "2ch": "2 Chronicles",
            "ezr": "Ezra",
            "ne": "Nehemiah",
            "et": "Esther",
            "job": "Job",
            "ps": "Psalms",
            "prv": "Proverbs",
            "ec": "Ecclesiastes",
            "so": "Song of Solomon",
            "is": "Isaiah",
            "jr": "Jeremiah",
            "lm": "Lamentations",
            "ez": "Ezekiel",
            "dn": "Daniel",
            "ho": "Hosea",
            "jl": "Joel",
            "am": "Amos",
            "ob": "Obadiah",
            "jo": "Jonah",
            "mi": "Micah",
            "na": "Nahum",
            "hk": "Habakkuk",
            "zp": "Zephaniah",
            "hg": "Haggai",
            "zc": "Zechariah",
            "ml": "Malachi",
            "mt": "Matthew",
            "mk": "Mark",
            "lk": "Luke",
            "jn": "John",
            "act": "Acts",
            "rm": "Romans",
            "1co": "1 Corinthians",
            "2co": "2 Corinthians",
            "gl": "Galatians",
            "eph": "Ephesians",
            "ph": "Philippians",
            "cl": "Colossians",
            "1ts": "1 Thessalonians",
            "2ts": "2 Thessalonians",
            "1tm": "1 Timothy",
            "2tm": "2 Timothy",
            "tt": "Titus",
            "phm": "Philemon",
            "hb": "Hebrews",
            "jm": "James",
            "1pe": "1 Peter",
            "2pe": "2 Peter",
            "1jo": "1 John",
            "2jo": "2 John",
            "3jo": "3 John",
            "jud": "Jude",
            "re": "Revelation",
        }

        cursor = self.conn.cursor()
        books_imported = 0
        verses_imported = 0

        logger.info("Importing books and verses...")

        for abbrev, chapters in normalized.items():
            book_name = abbrev_to_name.get(abbrev)
            if not book_name:
                logger.warning(f"Unknown book abbreviation: {abbrev}, skipping")
                continue

            # Determine testament
            testament = "Old Testament" if books_imported < 39 else "New Testament"

            # Insert book
            cursor.execute(
                "INSERT OR IGNORE INTO books (name, testament, order_index) VALUES (?, ?, ?)",
                (book_name, testament, books_imported + 1),
            )
            book_id = cursor.execute(
                "SELECT id FROM books WHERE name = ?", (book_name,)
            ).fetchone()[0]
            books_imported += 1

            # Insert verses
            for chapter, verse_texts in chapters.items():
                for verse, text in enumerate(verse_texts, 1):
                    if text is None:
                        continue
                    cursor.execute(
                        "INSERT OR REPLACE INTO verses (book_id, chapter, verse, text) VALUES (?, ?, ?, ?)",
                        (book_id, chapter, verse, text),
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


def main():
    parser = argparse.ArgumentParser(description="Import Bible translation into SQLite database")
    parser.add_argument(
        "--translation", default="kjv", help="Translation identifier (e.g., 'kjv', 'niv')"
    )
    parser.add_argument("--source", required=True, help="Path to source JSON file")
    parser.add_argument(
        "--output",
        default="bible_data/biblelive.sqlite",
        help="Output database path (default: bible_data/biblelive.sqlite)",
    )
    parser.add_argument(
        "--info", action="store_true", help="Show database info and exit (no import)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Force reimport even if database exists"
    )

    args = parser.parse_args()

    importer = BibleImporter(args.output)

    try:
        importer.connect()

        # Info mode - just show database information
        if args.info:
            importer.show_info()
            return 0

        # Check if already imported
        db_exists = Path(args.output).exists()
        if db_exists and not args.force:
            try:
                metadata = importer.get_metadata()
                if metadata.get("translation"):
                    logger.warning(
                        f"Database already exists with translation: {metadata.get('translation')}"
                    )
                    logger.warning("Use --force to reimport or --info to view details")
                    return 1
            except sqlite3.OperationalError:
                # No metadata table, proceed with import
                pass

        # Create schema and import
        logger.info(f"Importing {args.translation} from {args.source}...")
        importer.create_schema()

        books, verses = importer.import_kjv_json(args.source)
        importer.set_metadata(args.translation, args.source)

        print("\n" + "=" * 60)
        print("✓ Import Complete!")
        print("=" * 60)
        print(f"Database:    {args.output}")
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


if __name__ == "__main__":
    sys.exit(main())
