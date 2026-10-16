"""
bible_lookup.py - Bible text lookup and validation system

Runtime Bible lookup optimized for read operations.
For importing Bible data, use: python -m core.import_bible
"""

import logging
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# KJV sources mark editorial matter with braces or brackets:
#   `{was}` / `[was]` - a word supplied by the translators, part of the text
#   `{firmament: Heb. expansion}` - a marginal note, not part of the text
# The first kind is unwrapped, the second dropped. None of it belongs on a
# projected slide.
_SUPPLIED_WORD = re.compile(r"[\[{]([^{}\[\]:]*)\s*[\]}]")
_MARGINAL_NOTE = re.compile(r"[\[{][^{}\[\]]*:[^{}\[\]]*[\]}]")
# e-Sword modules prefix verses with a pilcrow and wrap the words of Christ in
# guillemets to mark red-letter text.
_PILCROW = re.compile(r"^[¶\s]+|[¶\s]+$")
_RED_LETTER_MARKS = str.maketrans("", "", "‹›‹›«»")


def clean_verse_text(text: str) -> str:
    """Strip editorial markup and normalize whitespace for display.

    Keeps translator-supplied words (``{was}`` -> ``was``), drops marginal notes
    (``{firmament: Heb. expansion}``), removes red-letter guillemets and
    leading/trailing pilcrows, then collapses runs of whitespace. Unbalanced
    braces are left alone rather than deleting surrounding text.
    """
    if not text:
        return ""
    cleaned = _MARGINAL_NOTE.sub("", text)
    cleaned = _SUPPLIED_WORD.sub(r"\1", cleaned)
    cleaned = cleaned.translate(_RED_LETTER_MARKS)
    cleaned = _PILCROW.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


# Upper bound on cached validation results. Cache keys are caller-controlled
# (any book string with any chapter/verse ints), so without a bound anyone who
# can reach the server could grow the dict without limit. 4096 entries is far
# beyond the working set; evicted entries are cheap to recompute.
_VALIDATION_CACHE_MAX_ENTRIES = 4096


class BibleLookup:
    """Runtime Bible text lookup and validation.

    This class assumes the database has already been imported using the import_bible CLI.
    It provides read-only access to Bible text for validation and retrieval.
    """

    def __init__(
        self, db_path: str = "bible_data/biblelive.sqlite", readonly: bool = False
    ) -> None:
        """Initialize the BibleLookup instance.

        Args:
            db_path: Path to the SQLite database file.
            readonly: If True, open database in read-only mode (recommended for production).
        """
        self.db_path = Path(db_path)
        self.readonly = readonly
        self.conn: sqlite3.Connection | None = None
        self._validation_cache: dict[tuple[str, int, int | None], bool] = {}
        self._connect_database()

        # Log database metadata if available
        try:
            metadata = self.get_metadata()
            if metadata:
                translation = metadata.get("translation", "unknown")
                logger.info(f"Bible database loaded: {translation} ({self.db_path})")
        except Exception:
            # Metadata table might not exist in older databases
            logger.warning(f"Bible database loaded without metadata: {self.db_path}")

    def _connect_database(self) -> None:
        """Connect to the Bible database.

        Opens the database in read-only mode if readonly=True, otherwise allows writes.
        Note: check_same_thread=False is used because the connection may be accessed
        from different threads (e.g., FastAPI worker threads). SQLite serializes access
        internally for short read operations.
        """
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Bible database not found at {self.db_path}. "
                f"Run: python -m core.import_bible --translation kjv --source data/kjv2.json"
            )

        if self.readonly:
            # Open in read-only mode (requires file:// URI)
            uri = f"file:{self.db_path}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            logger.debug(f"Connected to database in read-only mode: {self.db_path}")
        else:
            # Open with write access (for compatibility with tests and old code)
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            logger.debug(f"Connected to database: {self.db_path}")

    def is_loaded(self) -> bool:
        """Return True if a database connection is open."""
        return self.conn is not None

    def ping(self) -> bool:
        """Return True if the database answers a trivial query."""
        if self.conn is None:
            return False
        try:
            self.conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def book_count(self) -> int:
        """Return the number of books in the database (0 if empty/unavailable)."""
        if self.conn is None:
            return 0
        try:
            row = self.conn.execute("SELECT COUNT(*) FROM books").fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    def verse_count(self) -> int:
        """Return the number of verses in the database (0 if empty/unavailable)."""
        if self.conn is None:
            return 0
        try:
            row = self.conn.execute("SELECT COUNT(*) FROM verses").fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    def get_metadata(self) -> dict:
        """Get metadata about this Bible database.

        Returns:
            Dictionary of metadata key-value pairs, or empty dict if no metadata exists.
        """
        if self.conn is None:
            return {}

        try:
            cursor = self.conn.cursor()
            rows = cursor.execute("SELECT key, value FROM bible_metadata").fetchall()
            return dict(rows)
        except sqlite3.OperationalError:
            # Metadata table doesn't exist (old database)
            return {}

    def load_kjv_from_json(self, json_path: str) -> None:
        """DEPRECATED: Use import_bible CLI instead.

        This method is kept for backwards compatibility but logs a warning.
        New code should use: python -m core.import_bible --source data/kjv2.json
        """
        logger.warning(
            "BibleLookup.load_kjv_from_json() is deprecated. "
            "Use 'python -m core.import_bible --translation kjv --source data/kjv2.json' instead."
        )
        # For backwards compatibility, we'll still attempt the import if called
        # This allows existing tests and scripts to continue working
        if __package__:
            from .import_bible import BibleImporter
        else:
            from import_bible import BibleImporter  # type: ignore[no-redef]

        importer = BibleImporter(str(self.db_path))
        try:
            importer.conn = self.conn  # Reuse existing connection
            importer.create_schema()
            importer.import_kjv_json(json_path)
            importer.set_metadata("kjv", json_path)
        except Exception as e:
            logger.error(f"Failed to load KJV from JSON: {e}")
            raise

    def _remember_validation(self, cache_key: tuple[str, int, int | None], result: bool) -> None:
        """Cache a validation result, evicting the oldest entry when full."""
        if (
            cache_key not in self._validation_cache
            and len(self._validation_cache) >= _VALIDATION_CACHE_MAX_ENTRIES
        ):
            self._validation_cache.pop(next(iter(self._validation_cache)))
        self._validation_cache[cache_key] = result

    def validate_reference(self, book: str, chapter: int, verse: int | None = None) -> bool:
        """
        Validate that a book:chapter[:verse] reference exists in the Bible.
        Returns True if valid, False otherwise.
        Uses instance-level caching for performance.
        """
        if not self.conn:
            return False

        # Create cache key
        cache_key = (book, chapter, verse)
        if cache_key in self._validation_cache:
            return self._validation_cache[cache_key]

        # Get book ID
        cursor = self.conn.execute("SELECT id FROM books WHERE name = ?", (book,))
        book_row = cursor.fetchone()
        if not book_row:
            self._remember_validation(cache_key, False)
            return False
        book_id = book_row[0]

        # Check if chapter exists
        cursor = self.conn.execute(
            "SELECT 1 FROM verses WHERE book_id = ? AND chapter = ? LIMIT 1",
            (book_id, chapter),
        )
        if not cursor.fetchone():
            self._remember_validation(cache_key, False)
            return False

        # If verse specified, check if it exists
        if verse is not None:
            cursor = self.conn.execute(
                "SELECT 1 FROM verses WHERE book_id = ? AND chapter = ? AND verse = ? LIMIT 1",
                (book_id, chapter, verse),
            )
            result = cursor.fetchone() is not None
            self._remember_validation(cache_key, result)
            return result

        result = True
        self._remember_validation(cache_key, result)
        return result

    def last_verse(self, book: str, chapter: int) -> int | None:
        """The highest verse number stored for ``book`` ``chapter``, or None if there is none."""
        if self.conn is None:
            return None
        try:
            row = self.conn.execute(
                "SELECT MAX(v.verse) FROM verses v JOIN books b ON b.id = v.book_id "
                "WHERE b.name = ? AND v.chapter = ?",
                (book, chapter),
            ).fetchone()
        except sqlite3.Error:
            return None
        return int(row[0]) if row and row[0] is not None else None

    def get_validator_hook(self) -> Callable:
        """
        Return a validator function that can be used with ReferenceParser.
        Returns True if reference is valid, False to drop it.
        """

        def validate_reference_obj(ref) -> bool:
            return self.validate_reference(ref.book, ref.chapter, ref.verse_start)

        return validate_reference_obj

    def get_verse_range(
        self, book: str, chapter: int, verse_start: int, verse_end: int | None = None
    ) -> list[tuple[int, str]]:
        """Get individual verses as ``(verse_number, text)`` pairs.

        Text is cleaned of editorial markup via :func:`clean_verse_text` so every
        consumer (display, operator console) sees the same plain text.

        Args:
            book: Book name (must match exactly as in database)
            chapter: Chapter number
            verse_start: Starting verse number
            verse_end: Optional ending verse number (inclusive). If None, only
                ``verse_start`` is returned.

        Returns:
            List of ``(verse, text)`` in ascending verse order. Empty when the
            book, chapter, or range has no rows.
        """
        if self.conn is None:
            return []
        try:
            book_row = self.conn.execute("SELECT id FROM books WHERE name = ?", (book,)).fetchone()
            if not book_row:
                return []
            book_id = book_row[0]

            if verse_end is None:
                verse_end = verse_start
            # Tolerate reversed ranges instead of returning nothing.
            if verse_start > verse_end:
                verse_start, verse_end = verse_end, verse_start

            rows = self.conn.execute(
                """
                SELECT verse, text FROM verses
                WHERE book_id = ? AND chapter = ? AND verse BETWEEN ? AND ?
                ORDER BY verse
                """,
                (book_id, chapter, verse_start, verse_end),
            ).fetchall()
            return [
                (int(verse), text)
                for verse, text in ((row[0], clean_verse_text(row[1] or "")) for row in rows)
                if text
            ]
        except Exception as e:
            logger.warning(f"Error fetching verse text: {e}")
            return []

    def get_verse_text(
        self, book: str, chapter: int, verse_start: int, verse_end: int | None = None
    ) -> str:
        """Get the text for a verse or range of verses.

        Args:
            book: Book name (must match exactly as in database)
            chapter: Chapter number
            verse_start: Starting verse number
            verse_end: Optional ending verse number (inclusive). If None, only verse_start is returned.

        Returns:
            Concatenated verse text(s) joined by spaces, or empty string if not found.
        """
        return " ".join(
            text for _, text in self.get_verse_range(book, chapter, verse_start, verse_end)
        )

    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()


# Example usage and testing
if __name__ == "__main__":
    # Initialize Bible lookup
    bible = BibleLookup()

    # If we had a JSON file, we could load it:
    # bible.load_kjv_from_json("path/to/kjv.json")

    # For now, let's test with some known good/bad references
    print("Bible lookup initialized")
    print(
        "To use: create BibleLookup instance and pass its.get_validator_hook() to ReferenceParser"
    )

    # Example:
    # parser = ReferenceParser(validator=bible.get_validator_hook())
    # refs = parser.parse("John 3:16")
    # print(refs)  # Will only return ref if John 3:16 exists in loaded Bible

    bible.close()
