"""Discover the Bible translations available to switch between at runtime.

Each translation is its own SQLite file in the same directory as the configured
database (``config.DB_PATH``), with the same ``books``/``verses`` schema and a
``bible_metadata`` row ``translation`` naming it (``kjv``, ``ltb``, ``web``...).
Only files that look like that are offered, so raw source dumps, ``.bak`` copies
and anything else in ``bible_data/`` are ignored. Files are opened read-only and
only to read their metadata.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Translation ids end up in a JSON file and a request body: keep them tame.
_VERSION_ID = re.compile(r"^[a-z0-9_-]{1,16}$")


@dataclass(frozen=True)
class BibleVersion:
    """One selectable translation: its id (the metadata ``translation``) and file."""

    id: str
    path: Path


def _read_translation(path: Path) -> str | None:
    """The ``translation`` id stored in ``path``, or None if it is not a Bible DB."""
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not {"books", "verses", "bible_metadata"} <= tables:
            return None
        row = conn.execute("SELECT value FROM bible_metadata WHERE key = 'translation'").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    value = str(row[0]).strip().lower() if row and row[0] else ""
    return value if _VERSION_ID.match(value) else None


def discover_versions(directory: Path) -> dict[str, BibleVersion]:
    """Translations found as ``*.sqlite`` files in ``directory``, keyed by id.

    When two files claim the same translation the first by filename wins and the
    clash is logged, so the choice is stable rather than filesystem-order luck.
    """
    versions: dict[str, BibleVersion] = {}
    if not directory.is_dir():
        return versions
    for path in sorted(directory.glob("*.sqlite")):
        translation = _read_translation(path)
        if translation is None:
            continue
        if translation in versions:
            logger.warning(
                f"Ignoring {path.name}: translation '{translation}' is already provided "
                f"by {versions[translation].path.name}"
            )
            continue
        versions[translation] = BibleVersion(id=translation, path=path)
    return versions
