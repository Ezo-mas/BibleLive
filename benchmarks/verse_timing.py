"""Verse timing: when each verse of a displayed chapter was read aloud.

The follow-along benchmark's truth with ``--truth timestamps``. The lag of a hint used
to be measured from the start of the final utterance that read its verse, which holds one
or two verses. Here the time is per verse: the spoken words of a display period (Kmynas
word timestamps, see ``follow_along_bench.py``) are aligned in order to the words of the
chapter's verses, and each verse gets the time of its first and last aligned word.

Words are the 5-letter stems of the 4+-letter words, as ``core.verse_text`` scores them
(folded, so diacritics do not count). Short words (under 4 letters) are left out on both
sides: they carry little and are said everywhere. The alignment is a local-in-the-speech,
global-in-the-verses dynamic program: the speaker's words between verses are free (never
aligned), a verse word that was not heard costs ``SKIP_COST``, and a heard word aligns to
an exact stem (``EXACT_SCORE``) or to one that differs by one letter (``NEAR_SCORE``, a
misheard letter). Pairs stay in order, so a verse cannot be aligned before the one before
it. A verse counts as read when ``coverage`` (aligned words over the verse's words) reaches
the bar and at least ``MIN_MATCHES`` of its words were heard.

Standard library only (plus ``core.verse_text`` for the normalisation); the Bible database
is opened read-only.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.verse_text import ordered_stems

# Alignment scores. A skipped verse word is a cost, so a word heard by chance far from its
# place in the verse does not pay; a heard word is worth more than skipping it.
EXACT_SCORE = 1.0
NEAR_SCORE = 0.6
SKIP_COST = 0.5
# Verses a display can be read on past its own last verse (the reading-on window).
READ_ON_VERSES = 10
# A verse is read only when this many of its words were heard (or all of them, if fewer).
MIN_MATCHES = 2
_STEM_LEN = 5

_PAIR, _SKIP, _UNUSED = 1, 0, 2  # back-pointer moves; _SKIP is 0 so a zeroed row means skip


@dataclass(frozen=True)
class SpokenWord:
    """A word of a final utterance with its time in the recording (seconds)."""

    text: str
    start_s: float
    end_s: float
    utterance: int  # the segmenter's utterance number


@dataclass(frozen=True)
class VerseRead:
    """One verse read aloud during a display period.

    ``start_s`` is the time of its first aligned word and ``end_s`` of its last one.
    ``aligned`` lists each aligned pair as (verse word stem, spoken word, start_s, end_s).
    """

    book: str
    chapter: int
    verse: int
    tokens: int  # the verse's words (stems)
    matched: int  # of them, aligned to a spoken word
    coverage: float  # matched / tokens
    start_s: float
    end_s: float
    text: str = ""  # the verse as the Bible database has it
    aligned: tuple[tuple[str, str, float, float], ...] = ()
    period_s: float | None = None  # show time of the display period it was read in


def reading_window(verse_start: int, verse_end: int | None, last_verse: int) -> range:
    """The verses of a chapter that a display can be read from: its first verse to its
    last verse plus ``READ_ON_VERSES``, within the chapter."""
    last = min(last_verse, (verse_end or verse_start) + READ_ON_VERSES)
    return range(verse_start, last + 1)


def chapter_verse_texts(db_path: str | Path, book: str, chapter: int) -> dict[int, str]:
    """verse number -> text for ``book`` ``chapter`` of a Bible database (read-only)."""
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT v.verse, v.text FROM verses v JOIN books b ON b.id = v.book_id "
            "WHERE b.name = ? AND v.chapter = ?",
            (book, chapter),
        ).fetchall()
    finally:
        conn.close()
    return {int(verse): text for verse, text in rows}


def _near(a: str, b: str) -> bool:
    """True when two 5-letter stems differ by one letter (a misheard letter)."""
    return len(a) == len(b) == _STEM_LEN and sum(x != y for x, y in zip(a, b, strict=True)) == 1


def _score(verse_word: str, heard: str) -> float:
    if verse_word == heard:
        return EXACT_SCORE
    return NEAR_SCORE if _near(verse_word, heard) else 0.0


def align(ref: Sequence[str], spoken: Sequence[str]) -> list[tuple[int, int, float]]:
    """Align the spoken words to the reference words, in order.

    Returns (reference index, spoken index, score) for each aligned pair, in order. The
    spoken words before, between and after the aligned ones are not aligned. Every
    reference word is either aligned or skipped at ``SKIP_COST``.
    """
    n, m = len(ref), len(spoken)
    prev = [0.0] * (m + 1)  # row i-1: the best score ending at each spoken index
    moves: list[bytearray] = []  # per row: how each cell was reached
    for i in range(1, n + 1):
        word = ref[i - 1]
        cur = [prev[0] - SKIP_COST] + [0.0] * m
        row = bytearray(m + 1)
        for j in range(1, m + 1):
            best, move = prev[j] - SKIP_COST, _SKIP
            gain = _score(word, spoken[j - 1])
            if gain and prev[j - 1] + gain >= best:
                best, move = prev[j - 1] + gain, _PAIR
            if cur[j - 1] > best:  # the spoken word is not part of the verse
                best, move = cur[j - 1], _UNUSED
            cur[j] = best
            row[j] = move
        moves.append(row)
        prev = cur

    pairs: list[tuple[int, int, float]] = []
    i, j = n, m
    while i > 0:
        move = moves[i - 1][j]
        if move == _PAIR:
            pairs.append((i - 1, j - 1, _score(ref[i - 1], spoken[j - 1])))
            i -= 1
            j -= 1
        elif move == _SKIP:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def period_verse_reads(
    *,
    book: str,
    chapter: int,
    spoken: Sequence[SpokenWord],
    texts: dict[int, str],
    verse_start: int,
    verse_end: int | None,
    read_bar: float,
) -> list[VerseRead]:
    """The verses of ``book`` ``chapter`` read in one display period, from its spoken words.

    ``spoken`` are the words of the period's utterances in time order; ``texts`` the
    chapter's verses. Only the reading window is aligned (see ``reading_window``), and only
    verses at or above ``read_bar`` coverage are returned.
    """
    if not texts or not spoken:
        return []
    window = [v for v in reading_window(verse_start, verse_end, max(texts)) if v in texts]
    ref_words = [(verse, stem) for verse in window for stem in ordered_stems(texts[verse])]
    heard = [(stem, word) for word in spoken for stem in ordered_stems(word.text)]
    pairs = align([stem for _, stem in ref_words], [stem for stem, _ in heard])

    totals = Counter(verse for verse, _ in ref_words)
    hits: dict[int, list[tuple[int, int]]] = defaultdict(list)  # verse -> (ref, spoken) indices
    for ref_index, spoken_index, _ in pairs:
        hits[ref_words[ref_index][0]].append((ref_index, spoken_index))

    reads = []
    for verse in window:
        tokens = totals[verse]
        aligned = hits.get(verse)
        if not tokens or not aligned:
            continue
        coverage = len(aligned) / tokens
        if coverage < read_bar or len(aligned) < min(MIN_MATCHES, tokens):
            continue
        first = heard[aligned[0][1]][1]
        last = heard[aligned[-1][1]][1]
        reads.append(
            VerseRead(
                book=book,
                chapter=chapter,
                verse=verse,
                tokens=tokens,
                matched=len(aligned),
                coverage=round(coverage, 3),
                start_s=first.start_s,
                end_s=last.end_s,
                text=texts[verse],
                aligned=tuple(
                    (
                        ref_words[ri][1],
                        heard[si][1].text,
                        heard[si][1].start_s,
                        heard[si][1].end_s,
                    )
                    for ri, si in aligned
                ),
            )
        )
    return reads
