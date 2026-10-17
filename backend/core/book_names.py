"""Book names from the Bible translation that is loaded.

The canon (:mod:`core.books`) and the database always use the English name, and
every request the server accepts names a book that way. This module only supplies
the *label* an audience or operator reads, so it can follow the translation on
screen without touching what is stored, validated, or looked up.

The labels are not kept here: a translation's ``books`` table may carry a
``local_name`` column (the Lithuanian ``ltb`` database does: "Pradžios", "Jono
evangelija", "Romiečiams"). They are read from the database file the server has
loaded, so the book names are always the ones printed in the Bible the verse text
comes from, and a translation without them (KJV) shows the canonical English name.
The file is opened read-only and only for this one query.

Search spellings are the Lithuanian parser's own stems
(``verse_parser_lt.BOOKS``), so the operator's search accepts exactly what the
speech parser does.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from .books import CANONICAL_BOOKS, find_book
from .verse_parser_lt import BOOKS as _LT_PARSER_BOOKS

logger = logging.getLogger(__name__)


def read_local_names(path: Path) -> dict[str, str]:
    """Canonical book name -> ``local_name`` from a translation's ``books`` table.

    Empty when the file has no ``local_name`` column, cannot be read, or names
    nothing canonical. Blank names and names for books outside the canon are
    skipped, so one bad row cannot put an empty label on the projector.
    """
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(books)")}
        if "local_name" not in columns:
            return {}
        rows = conn.execute("SELECT name, local_name FROM books").fetchall()
    except sqlite3.Error as e:
        logger.warning(f"Could not read book names from {path.name}: {e!r}")
        return {}
    finally:
        conn.close()

    names: dict[str, str] = {}
    for name, local_name in rows:
        book = find_book(name) if isinstance(name, str) else None
        label = local_name.strip() if isinstance(local_name, str) else ""
        if book is not None and label:
            names[book.name] = label
    return names


class BookNames:
    """Book labels per database file, read on first use.

    Cached by file and modification time, so a translation that is switched to is
    read once, and a database re-imported with corrected names is picked up
    without a restart.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int], dict[str, str]] = {}

    def for_database(self, db_path: str | os.PathLike[str]) -> dict[str, str]:
        """Every canonical name -> its label in the database at ``db_path``."""
        path = Path(db_path)
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return {}
        if key not in self._cache:
            # A changed file replaces its older entry rather than piling up.
            self._cache = {k: v for k, v in self._cache.items() if k[0] != key[0]}
            self._cache[key] = read_local_names(path)
        return self._cache[key]

    def label(self, book: str, db_path: str | os.PathLike[str]) -> str | None:
        """The label for canonical ``book``, or None to show the canonical name."""
        return self.for_database(db_path).get(book)


def lithuanian_search_variants() -> dict[str, list[str]]:
    """Extra spellings the operator's search accepts for each canonical book name.

    The Lithuanian parser's stems ("pradz", "korintiec"), plus the same stems with
    a leading number for numbered books ("1 korintiec").
    """
    variants: dict[str, list[str]] = {}
    for book, (_name, _chapters, stems) in zip(CANONICAL_BOOKS, _LT_PARSER_BOOKS, strict=True):
        number = book.name.split(" ", 1)[0] if book.name[0].isdigit() else ""
        forms = list(stems)
        if number:
            forms += [f"{number} {stem}" for stem in stems]
        variants[book.name] = forms
    return variants
