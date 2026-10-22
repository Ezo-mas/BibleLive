"""
verse_text.py - pick the verse a speaker is reading from its words.

Preachers often name only a chapter ("Luko 16 skyrius") and then read the
verse aloud, or Whisper mangles the verse number ("Šimtoje eilutėje" for
"dešimtoje"). The words read aloud still identify the verse: score every
verse of the named chapter by how many of its rarer words were spoken.

Matching is chapter-scoped on purpose: across the whole Bible, ordinary
sermon sentences match some verse far too often to be useful.

Scoring: words are folded (no diacritics), words under 4 letters dropped,
and the rest cut to a 5-letter stem, since Lithuanian inflects the ending.
A verse scores the summed IDF of its stems that were spoken, divided by the
summed IDF of all its stems. Stems common across the Bible (IDF <= 2) never
count as heard, so they only weigh a little in the divisor: a verse scores
close to 1.0 when every distinctive word was heard.

Standard library only; the database is opened read-only.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .books import CANONICAL_BOOKS
    from .verse_parser_lt import fold
else:
    from books import CANONICAL_BOOKS  # type: ignore[no-redef]
    from verse_parser_lt import fold  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z]+")
_MIN_WORD_LEN = 4
_STEM_LEN = 5
_MIN_IDF = 2.0


def stems(text: str) -> set[str]:
    """Distinct 5-letter stems of the 4+-letter words in ``text``."""
    return {w[:_STEM_LEN] for w in _WORD.findall(fold(text).lower()) if len(w) >= _MIN_WORD_LEN}


def ordered_stems(text: str) -> list[str]:
    """The 5-letter stems of the 4+-letter words in ``text``, in reading order, duplicates kept."""
    return [w[:_STEM_LEN] for w in _WORD.findall(fold(text).lower()) if len(w) >= _MIN_WORD_LEN]


# One verse inside a chapter: (verse, its distinct stems, its summed IDF, its stems in order).
_Verse = tuple[int, set[str], float, tuple[str, ...]]


@dataclass(frozen=True)
class VerseMatch:
    verse: int
    verse_end: int | None  # last verse when the following ones were read too
    score: float  # 0..1, share of the verse's distinctive words that were heard
    runner_up: float  # best score among verses outside the matched range


class VerseTextIndex:
    """IDF index over one translation's verses, queried one chapter at a time."""

    def __init__(self, rows: list[tuple[str, int, int, str]]):
        """``rows`` are (canonical book name, chapter, verse, text)."""
        df: dict[str, int] = defaultdict(int)
        # One string object per distinct stem, shared by every verse that uses it.
        vocab: dict[str, str] = {}
        parsed: list[tuple[str, int, int, tuple[str, ...], set[str]]] = []
        for book, chapter, verse, text in rows:
            tokens = tuple(vocab.setdefault(w, w) for w in ordered_stems(text))
            s = set(tokens)
            parsed.append((book, chapter, verse, tokens, s))
            for w in s:
                df[w] += 1
        n = max(len(parsed), 1)
        self._idf = {w: math.log(n / c) for w, c in df.items()}
        self._chapters: dict[tuple[str, int], list[_Verse]] = defaultdict(list)
        for book, chapter, verse, tokens, s in parsed:
            norm = sum(self._idf[w] for w in s) or 1.0
            self._chapters[(book, chapter)].append((verse, s, norm, tokens))

    @classmethod
    def from_sqlite(cls, db_path: str | Path) -> VerseTextIndex:
        """Load a database built by ``core.import_bible`` (read-only)."""
        uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            rows = conn.execute(
                "SELECT b.name, v.chapter, v.verse, v.text "
                "FROM verses v JOIN books b ON b.id = v.book_id"
            ).fetchall()
        finally:
            conn.close()
        logger.info("Verse text index: %d verses from %s", len(rows), db_path)
        canonical = {book.name for book in CANONICAL_BOOKS}
        if rows and not any(book in canonical for book, *_ in rows):
            logger.warning(
                "Verse text database %s names no book by its canonical English name, "
                "so no verse will ever be matched",
                db_path,
            )
        return cls(rows)

    def scores(self, book: str, chapter: int, text: str) -> dict[int, float]:
        """Score ``text`` against every verse of ``book`` ``chapter`` (0..1 each).

        Empty when the chapter is unknown or ``text`` has no distinctive word.
        """
        verses = self._chapters.get((book, chapter))
        if not verses:
            return {}
        heard = {w for w in stems(text) if self._idf.get(w, 0.0) > _MIN_IDF}
        if not heard:
            return {}
        return {verse: sum(self._idf[w] for w in heard & s) / norm for verse, s, norm, _ in verses}

    def ordered(self, book: str, chapter: int) -> dict[int, tuple[str, ...]]:
        """Each verse's stems in reading order (as :func:`ordered_stems`), keyed by verse.

        Empty when the chapter is unknown. The dict is built on each call.
        """
        verses = self._chapters.get((book, chapter), ())
        return {verse: tokens for verse, _s, _norm, tokens in verses}

    def idf(self, stem: str) -> float:
        """The inverse document frequency of ``stem`` over the verses; 0.0 when none has it."""
        return self._idf.get(stem, 0.0)

    def best_verse(
        self, book: str, chapter: int, text: str, extend_min: float = 0.5
    ) -> VerseMatch | None:
        """The verse of ``book`` ``chapter`` whose words best match ``text``.

        Reading usually runs on into the next verse: each following verse
        that also scores at least ``extend_min`` extends the match to a range.
        """
        scores = self.scores(book, chapter, text)
        if not scores:
            return None
        verse = max(scores, key=lambda v: (scores[v], -v))
        if scores[verse] <= 0.0:
            return None
        end = verse
        while scores.get(end + 1, 0.0) >= extend_min:
            end += 1
        runner_up = max(
            (sc for v, sc in scores.items() if not verse <= v <= end),
            default=0.0,
        )
        return VerseMatch(
            verse=verse,
            verse_end=end if end > verse else None,
            score=scores[verse],
            runner_up=runner_up,
        )
