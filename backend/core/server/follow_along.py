"""Follow-along: which verse a speaker reads next, after the passage on screen.

Preachers often read on past the passage that is displayed (Luke 16:10, then 11,
12 ...). For each confident transcript, the verses just after the displayed
passage are scored with the IDF word overlap of :mod:`core.verse_text`, and a verse
that scores well enough becomes a hint for operators. The operator decides whether
to show it; nothing here touches a display.

The transcript is used for matching only. It is never stored, logged, or passed on.
"""

import asyncio
import logging
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from ..bible_lookup import BibleLookup
from ..bible_versions import BibleVersion
from ..config import config
from ..verse_text import VerseTextIndex
from .models import BibleReference, FollowAlongHint

logger = logging.getLogger(__name__)

# Share of a transcript that a verse's distinctive words must explain for a hint.
FOLLOW_ALONG_MIN_SCORE = 0.5
# How many verses past the displayed passage a hint may point to.
FOLLOW_ALONG_LOOKAHEAD = 8


class VerseIndexes:
    """Verse-text indexes for the spoken languages, each loaded on first use.

    A language's index comes from the translation ``config.LANGUAGE_BIBLE_VERSIONS``
    names for it, found through ``find_versions`` (the installed translations). A
    load takes about 2 s and ~45 MB, so it runs in a worker thread, and only once.
    A translation that is not installed, or a file that will not load, is logged
    once and then remembered as unavailable for that language.
    """

    def __init__(self, find_versions: Callable[[], dict[str, BibleVersion]]) -> None:
        self._find_versions = find_versions
        self._lock = threading.Lock()
        # Keyed by spoken language; None means follow-along cannot run for it.
        self._by_language: dict[str, VerseTextIndex | None] = {}
        # Keyed by file, so two languages naming the same translation share one load.
        self._by_path: dict[Path, VerseTextIndex] = {}

    async def for_language(self, language: str) -> VerseTextIndex | None:
        """The index for ``language``, or None when follow-along cannot run for it."""
        # Only configured languages are cached, so a client cannot grow the cache
        # by sending arbitrary language codes.
        if language not in config.LANGUAGE_BIBLE_VERSIONS:
            return None
        if language in self._by_language:
            return self._by_language[language]
        return await asyncio.to_thread(self._load, language)

    def _load(self, language: str) -> VerseTextIndex | None:
        """Load the index for ``language`` (worker thread); concurrent callers wait."""
        with self._lock:
            if language not in self._by_language:
                self._by_language[language] = self._build(language)
            return self._by_language[language]

    def _build(self, language: str) -> VerseTextIndex | None:
        version_id = config.LANGUAGE_BIBLE_VERSIONS[language]
        version = self._find_versions().get(version_id)
        if version is None:
            logger.warning(
                f"Follow-along is off for language '{language}': "
                f"Bible translation '{version_id}' is not installed"
            )
            return None
        index = self._by_path.get(version.path)
        if index is None:
            try:
                index = VerseTextIndex.from_sqlite(version.path)
            except (OSError, sqlite3.Error) as e:
                logger.warning(
                    f"Follow-along is off for language '{language}': "
                    f"could not read {version.path.name}: {e}"
                )
                return None
            self._by_path[version.path] = index
        return index


def next_verse_hint(
    index: VerseTextIndex, bible: BibleLookup, display: BibleReference, transcript: str
) -> FollowAlongHint | None:
    """The hint for the verse ``transcript`` reads after ``display``, if there is one.

    Candidates are the ``FOLLOW_ALONG_LOOKAHEAD`` verses after the displayed passage,
    and the best of them must reach ``FOLLOW_ALONG_MIN_SCORE``. The hint keeps the
    operator's page size (the on-screen width) and ends at the last verse of the
    chapter in the active Bible, because a Show click resolves against that Bible.
    """
    start = display.verse_start
    if start is None:
        return None
    end = display.verse_end or start
    scores = index.scores(display.book, display.chapter, transcript)
    candidates = [
        verse
        for verse, score in scores.items()
        if end < verse <= end + FOLLOW_ALONG_LOOKAHEAD and score >= FOLLOW_ALONG_MIN_SCORE
    ]
    if not candidates:
        return None
    verse = max(candidates, key=lambda v: (scores[v], -v))
    if not bible.validate_reference(display.book, display.chapter, verse):
        return None
    last = bible.last_verse(display.book, display.chapter)
    if last is None:
        return None
    width = end - start + 1
    verse_end = min(verse + width - 1, last)
    # Some translations skip verses (WEB has no Acts 8:37); a Show click needs
    # both ends to exist.
    while verse_end > verse and not bible.validate_reference(
        display.book, display.chapter, verse_end
    ):
        verse_end -= 1
    return FollowAlongHint(
        book=display.book,
        chapter=display.chapter,
        verse_start=verse,
        verse_end=verse_end,
        display_id=display.id,
    )
