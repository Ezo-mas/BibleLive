"""Discover the Bible translations available to switch between at runtime.

Each translation is its own SQLite file in the same directory as the configured
database (``config.DB_PATH``) or in the per-user Bibles folder
(``config.USER_BIBLE_DIR``, where translations that are not shipped with the app
are added on each machine), with the same ``books``/``verses`` schema and a
``bible_metadata`` row ``translation`` naming it (``kjv``, ``ltb``, ``web``...).
Only files that look like that are offered, so raw source dumps, ``.bak`` copies
and anything else in ``bible_data/`` are ignored. Files are opened read-only and
only to read their metadata.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Translation ids end up in a JSON file and a request body: keep them tame.
_VERSION_ID = re.compile(r"^[a-z0-9_-]{1,16}$")


@dataclass(frozen=True)
class BibleVersion:
    """One selectable translation: its id (the metadata ``translation``) and file."""

    id: str
    path: Path


class _IncompleteScan(Exception):
    """A scan with SQLite errors; return its partial results without caching them."""

    def __init__(self, versions: dict[str, BibleVersion]) -> None:
        self.versions = versions


def _read_translation(path: Path) -> str | None:
    """The ``translation`` id stored in ``path``, or None if it is not a Bible DB."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not {"books", "verses", "bible_metadata"} <= tables:
            return None
        row = conn.execute("SELECT value FROM bible_metadata WHERE key = 'translation'").fetchone()
    finally:
        conn.close()
    value = str(row[0]).strip().lower() if row and row[0] else ""
    return value if _VERSION_ID.match(value) else None


def discover_versions(directory: Path) -> dict[str, BibleVersion]:
    """Translations found as ``*.sqlite`` files in ``directory``, keyed by id.

    When two files claim the same translation the first by filename wins and the
    clash is logged, so the choice is stable rather than filesystem-order luck.
    """
    if not directory.is_dir():
        return {}
    signature = []
    for path in sorted(directory.glob("*.sqlite")):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue  # A translation was removed while listing the directory.
        signature.append((path.name, stat.st_mtime_ns, stat.st_size))
    try:
        versions = _discover_versions(directory, tuple(signature))
    except _IncompleteScan as exc:
        versions = exc.versions
    return versions.copy()


def discover_versions_in(directories: Iterable[Path]) -> dict[str, BibleVersion]:
    """Translations from several folders; an earlier folder wins a clash (logged).

    The app's own folder comes first, so a file added by hand cannot replace a shipped
    translation; it can only add one.
    """
    versions: dict[str, BibleVersion] = {}
    seen: set[Path] = set()
    for directory in directories:
        key = directory.resolve()
        if key in seen:
            continue
        seen.add(key)
        for translation, version in discover_versions(directory).items():
            if translation in versions:
                logger.warning(
                    f"Ignoring {version.path}: translation '{translation}' is already "
                    f"provided by {versions[translation].path}"
                )
                continue
            versions[translation] = version
    return versions


@lru_cache(maxsize=32)
def _discover_versions(
    directory: Path, signature: tuple[tuple[str, int, int], ...]
) -> dict[str, BibleVersion]:
    """Read metadata once per folder signature; callers receive a separate dict."""
    versions: dict[str, BibleVersion] = {}
    incomplete = False
    for name, _mtime, _size in signature:
        path = directory / name
        try:
            translation = _read_translation(path)
        except sqlite3.Error:
            incomplete = True
            continue
        if translation is None:
            continue
        if translation in versions:
            logger.warning(
                f"Ignoring {path.name}: translation '{translation}' is already provided "
                f"by {versions[translation].path.name}"
            )
            continue
        versions[translation] = BibleVersion(id=translation, path=path)
    if incomplete:
        # lru_cache does not cache exceptions, so the next call retries the scan.
        raise _IncompleteScan(versions)
    return versions
