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
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..bible_lookup import BibleLookup
from ..bible_versions import BibleVersion
from ..config import config
from ..verse_text import VerseTextIndex, ordered_stems
from .models import BibleReference, FollowAlongHint

logger = logging.getLogger(__name__)

# Share of a transcript that a verse's distinctive words must explain for a hint.
FOLLOW_ALONG_MIN_SCORE = 0.5
# How many verses past the displayed passage a hint may point to.
FOLLOW_ALONG_LOOKAHEAD = 8
# Armed (the audio pipeline should send partial transcripts) for this long after a
# passage with a verse is first seen, and for ARM_HOLD_S after a transcript reads a
# verse in the window a hint could point to.
ARM_AFTER_DISPLAY_S = 60.0
ARM_HOLD_S = 30.0
# After a hint, no verse below it is offered for this long. A partial transcript can offer
# a verse that the final of the same utterance then scores below (partial 12, final 11).
REGRESS_HOLD_S = 15.0


@dataclass(frozen=True)
class HintPolicy:
    """The knobs of :func:`next_verse_hint`. The defaults are the shipped behaviour."""

    min_score: float = FOLLOW_ALONG_MIN_SCORE
    # The bar for the sequentially next verse (see next_verse_hint); None = min_score.
    next_min_score: float | None = None
    # "best": the highest score wins, ties going to the lower verse.
    # "furthest": the highest verse that clears its bar wins. Not the default: a later
    # verse worded like the one being read (Matthew 7:24 "... ir juos vykdo" and 7:26
    # "... ir jų nevykdo") clears the bar on the current verse's words and would win.
    # The start rule does not need it either: the verse it starts is the next one.
    pick: Literal["best", "furthest"] = "best"
    lookahead: int = FOLLOW_ALONG_LOOKAHEAD
    # Verse-start rule (on since the follow-along benchmark of 2026-10-22, see
    # benchmarks/BASELINE_NOTES.md): a verse also counts as read when its first words
    # are heard in order (see verse_started). start_prefix is how many of its first stems
    # are considered, start_min_tokens how many of those must be heard, start_min_idf the
    # summed IDF of the heard ones, and start_anchor the latest position (counted from 0)
    # at which the first stem heard may fall.
    # A start only counts for the sequentially next verse, and only while the verse before
    # it is being read too: heard in the same transcript with at least start_prev_min of
    # its words (as index.scores), or the verse follow-along itself last offered. A later
    # verse that merely opens with words of the one being read ("Viešpaties angelas ... iš
    # dangaus" in Genesis 22:11 and 22:15) is then never taken for a start.
    start_match: bool = True
    start_prefix: int = 6
    start_min_tokens: int = 3
    start_min_idf: float = 8.0
    start_anchor: int = 2
    start_prev_min: float = 0.25


# Stems a start alignment may skip in a row, on the verse side and on the transcript side.
START_MAX_GAP = 2


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


def verse_started(
    index: VerseTextIndex,
    book: str,
    chapter: int,
    verse: int,
    transcript_tokens: Sequence[str],
    policy: HintPolicy,
) -> bool:
    """Whether the transcript reads the start of ``verse``: its first words, heard in order.

    ``transcript_tokens`` are the transcript's stems in reading order (``ordered_stems``).
    Of the verse's first ``policy.start_prefix`` stems, at least ``policy.start_min_tokens``
    must be heard, in the same order, and their summed IDF must reach ``start_min_idf``. A
    stem read differently or left out, and a word the speaker adds, do not break the order,
    as long as no more than ``START_MAX_GAP`` stems are skipped in a row on either side.
    The first stem heard must be among the verse's first ``start_anchor + 1`` stems, so a
    run of words from the middle of a verse is not a start. An opening that many verses
    share, such as "Jėzus atsakė", weighs under the bar on its own, so it needs a further
    stem of the verse to be heard too.
    """
    prefix = index.ordered(book, chapter).get(verse, ())[: policy.start_prefix]
    weights = [index.idf(stem) for stem in prefix]
    totals = _in_order_totals(prefix, weights, transcript_tokens, policy.start_anchor)
    return any(
        count >= policy.start_min_tokens and total >= policy.start_min_idf
        for count, total in totals.items()
    )


def _in_order_totals(
    prefix: Sequence[str],
    weights: Sequence[float],
    transcript_tokens: Sequence[str],
    anchor: int,
) -> dict[int, float]:
    """For each count, the largest summed weight of ``prefix`` stems heard in order.

    An alignment pairs prefix positions with transcript positions, increasing on both
    sides, with at most ``START_MAX_GAP`` unpaired stems between two pairs on either side,
    and its first pair at a prefix position no later than ``anchor``. Only pairs of equal
    stems are considered, so the work follows the number of such pairs, not the length of
    the transcript.
    """
    wanted = set(prefix)
    where: dict[str, list[int]] = {}
    for q, token in enumerate(transcript_tokens):
        if token in wanted:
            where.setdefault(token, []).append(q)
    # reach[(i, q)][c]: the largest summed weight of c pairs whose last pair puts prefix[i]
    # at transcript[q].
    reach: dict[tuple[int, int], dict[int, float]] = {}
    totals: dict[int, float] = {}
    # Prefix positions are visited in order, so every cell that can precede a cell is in
    # reach before it.
    for i, token in enumerate(prefix):
        for q in where.get(token, ()):
            cell: dict[int, float] = {}
            if i <= anchor:
                cell[1] = weights[i]
            for (pi, pq), counts in reach.items():
                if 0 < i - pi <= START_MAX_GAP + 1 and 0 < q - pq <= START_MAX_GAP + 1:
                    for c, total in counts.items():
                        value = total + weights[i]
                        if value > cell.get(c + 1, -1.0):
                            cell[c + 1] = value
            if cell:
                reach[(i, q)] = cell
                for c, total in cell.items():
                    if total > totals.get(c, -1.0):
                        totals[c] = total
    return totals


def next_verse_hint(
    index: VerseTextIndex,
    bible: BibleLookup,
    display: BibleReference,
    transcript: str,
    policy: HintPolicy = HintPolicy(),
    offered: Collection[int] = frozenset(),
    latest: int | None = None,
    floor: int | None = None,
    read_through: int | None = None,
) -> FollowAlongHint | None:
    """The hint for the verse ``transcript`` reads after ``display``, if there is one.

    Candidates are the ``policy.lookahead`` verses after the displayed passage's last verse.
    With ``policy.start_match``, the sequentially next verse is a candidate whatever its
    score when its start is heard (see :func:`verse_started`) while the verse before it is
    read: heard with ``policy.start_prev_min`` of its words, or ``read_through`` (the verse
    follow-along last saw read) is that verse. ``offered`` holds the verse starts already hinted for
    this display, and none of them is offered again. ``floor``, when set, is the lowest verse
    a hint may point to. ``latest`` is the most recent verse hinted for this display: the
    sequentially next verse, after the later of ``latest`` and the passage's last verse,
    needs ``policy.next_min_score`` (when set), the other candidates ``policy.min_score``;
    ``policy.pick`` chooses among them. The hint keeps the operator's page size (the on-screen
    width) and ends at the last verse of the chapter in the active Bible, because a Show
    click resolves against that Bible.
    """
    start = display.verse_start
    if start is None:
        return None
    end = display.verse_end or start
    sequential = end if latest is None else max(end, latest)
    next_bar = policy.min_score if policy.next_min_score is None else policy.next_min_score
    scores = index.scores(display.book, display.chapter, transcript)
    started: set[int] = set()
    following = sequential + 1
    previous_read = (
        scores.get(sequential, 0.0) >= policy.start_prev_min or read_through == sequential
    )
    if (
        policy.start_match
        and previous_read
        and verse_started(
            index, display.book, display.chapter, following, ordered_stems(transcript), policy
        )
    ):
        started = {following}
    candidates = [
        verse
        for verse in sorted(scores.keys() | started)
        if end < verse <= end + policy.lookahead
        and verse not in offered
        and (floor is None or verse >= floor)
        and (
            verse in started
            or scores.get(verse, 0.0) >= (next_bar if verse == sequential + 1 else policy.min_score)
        )
    ]
    if not candidates:
        return None
    if policy.pick == "furthest":
        verse = max(candidates)
    else:
        verse = max(candidates, key=lambda v: (scores.get(v, 0.0), -v))
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


class FollowAlong:
    """Hint policy plus the armed state. Pure: no I/O and no clock; callers pass ``now``.

    Armed means the passage on screen may still be read, so the audio pipeline should
    send partial transcripts. It holds for ``arm_after_display_s`` after a display is
    first seen, and for ``arm_hold_s`` after a transcript reads a verse in the window a
    hint could point to. State is kept for the current display only: a new display id
    starts afresh. Within a display, a verse is offered at most once, and for
    ``regress_hold_s`` after a hint no verse below it is offered.
    """

    def __init__(
        self,
        policy: HintPolicy = HintPolicy(),
        arm_after_display_s: float = ARM_AFTER_DISPLAY_S,
        arm_hold_s: float = ARM_HOLD_S,
        regress_hold_s: float = REGRESS_HOLD_S,
    ) -> None:
        self.policy = policy
        self.arm_after_display_s = arm_after_display_s
        self.arm_hold_s = arm_hold_s
        self.regress_hold_s = regress_hold_s
        # The hint last returned for the current display; the snapshot resend reads it.
        self.last_hint: FollowAlongHint | None = None
        self._display_id: str | None = None
        self._first_seen = 0.0
        # The verse starts hinted for the current display, and the latest hint's verse
        # start and time.
        self._offered: set[int] = set()
        self._last_offer: tuple[int, float] | None = None
        # The current display's first verse when it puts up the hint offered just before it.
        self._carried_read: int | None = None
        # When a transcript last read a verse in the window, for the current display.
        self._last_reading: float | None = None

    def on_transcript(
        self,
        index: VerseTextIndex | None,
        bible: BibleLookup,
        display: BibleReference | None,
        transcript: str,
        now: float,
    ) -> tuple[FollowAlongHint | None, bool]:
        """The hint to send now (or None), and whether follow-along is armed.

        ``now`` is a monotonic clock reading in seconds. The transcript is used for
        matching only; it is not kept.
        """
        if display is None or display.verse_start is None or index is None:
            return None, False
        if display.id != self._display_id:
            # A display that puts up the verse follow-along just offered (a Show click or
            # auto-show) starts on a verse already seen read, so the next verse's start can
            # count at once (see HintPolicy.start_prev_min). In effect a one-verse feature:
            # the next verse follows the display's last verse, and only its first was read.
            hint = self.last_hint
            self._carried_read = (
                display.verse_start
                if hint is not None
                and (hint.book, hint.chapter, hint.verse_start)
                == (display.book, display.chapter, display.verse_start)
                else None
            )
            self._display_id = display.id
            self._first_seen = now
            self._offered = set()
            self._last_offer = None
            self._last_reading = None
            self.last_hint = None
        start = display.verse_start
        end = display.verse_end or start
        read_bar = self.policy.min_score
        if self.policy.next_min_score is not None:
            read_bar = min(read_bar, self.policy.next_min_score)
        scores = index.scores(display.book, display.chapter, transcript)
        reach = end + self.policy.lookahead
        read = any(score >= read_bar for verse, score in scores.items() if start <= verse <= reach)
        if not read and self.policy.start_match:
            tokens = ordered_stems(transcript)
            read = any(
                verse_started(index, display.book, display.chapter, verse, tokens, self.policy)
                for verse in range(start, reach + 1)
            )
        if read:
            self._last_reading = now
        latest = None
        floor = None
        # The verse follow-along last saw read (its latest hint here, or the hinted verse
        # this display put up) lets the next verse's start count, but only for arm_hold_s:
        # a quote of that start much later is not the reading going on.
        read_through = None
        if self._last_offer is not None:
            latest, offered_at = self._last_offer
            if now - offered_at < self.regress_hold_s:
                floor = latest
            if now - offered_at < self.arm_hold_s:
                read_through = latest
        elif self._carried_read is not None and now - self._first_seen < self.arm_hold_s:
            read_through = self._carried_read
        hint = next_verse_hint(
            index,
            bible,
            display,
            transcript,
            self.policy,
            self._offered,
            latest,
            floor,
            read_through,
        )
        if hint is not None:
            self._offered.add(hint.verse_start)
            self._last_offer = (hint.verse_start, now)
            self.last_hint = hint
        armed = now - self._first_seen < self.arm_after_display_s or (
            self._last_reading is not None and now - self._last_reading < self.arm_hold_s
        )
        return hint, armed
