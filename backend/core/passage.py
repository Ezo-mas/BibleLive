"""Resolve an operator-supplied passage (book/chapter/verse) to displayable text.

Manual scripture selection is a second, explicit entry point to the display that
does not involve the speech-to-text pending queue. The HTTP lookup endpoint and
the ``display_reference`` WebSocket action both resolve passages through here, so
they cannot disagree about what a valid passage is or which text it contains.

Books come from the shared canon in :mod:`core.books` and text from the same
read-only ``BibleLookup`` connection the rest of the server uses. This module
owns no data of its own and performs no writes, so a manual request cannot
change the database or the pending queue.

Failure convention, used by both callers:

* ``422`` — the request breaks a rule that is knowable without the database
  (wrong type, out-of-range chapter, reversed range, range longer than the
  configured maximum).
* ``404`` — the request is well formed but that data does not exist (unknown
  book, a chapter or verse the imported text does not have).
* ``503`` — the server has no Bible text loaded at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from .bible_lookup import BibleLookup
from .books import find_book
from .config import config


class PassageError(Exception):
    """A passage request that cannot be satisfied.

    ``status_code`` follows the convention above so the HTTP endpoint can map a
    rejection straight onto a status, while the WebSocket path reports
    ``message`` to the operator unchanged.
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class ResolvedPassage:
    """A passage that exists in the server's Bible text.

    ``verse_start``/``verse_end`` are the requested (and validated) bounds; the
    individual verses are what the database actually holds, in ascending order.
    """

    book: str
    chapter: int
    verse_start: int
    verse_end: int
    verses: tuple[tuple[int, str], ...]

    @property
    def text(self) -> str:
        """The verses joined for display, matching the approval path's format."""
        return " ".join(text for _, text in self.verses)


def _positive_int(value: object, label: str) -> int:
    """Return ``value`` as a positive int, or raise a 422 for anything else."""
    # bool is an int subclass, and True/False are never verse numbers.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PassageError(422, f"{label} must be a whole number of 1 or more.")
    return value


def resolve_passage(
    lookup: BibleLookup,
    book: object,
    chapter: object,
    verse_start: object = None,
    verse_end: object = None,
) -> ResolvedPassage:
    """Validate and resolve a passage request against the canon and database.

    Args:
        lookup: The server's loaded Bible lookup.
        book: Canonical book name, e.g. ``"John"``.
        chapter: Chapter number, ``1``..the book's chapter count.
        verse_start: First verse; ``None`` means verse 1.
        verse_end: Last verse (inclusive); ``None`` means a single verse.

    Returns:
        The resolved passage with cleaned verse text.

    Raises:
        PassageError: Described by its ``status_code`` and human-readable
            ``message`` (see the module docstring).
    """
    if not isinstance(book, str):
        raise PassageError(422, "A book name is required.")

    canonical = find_book(book)
    if canonical is None:
        raise PassageError(
            404,
            f"Unknown book '{book.strip()}'. Choose one of the 66 canonical book names.",
        )

    chapter_number = _positive_int(chapter, "Chapter")
    if chapter_number > canonical.chapters:
        raise PassageError(
            422,
            f"{canonical.name} has {canonical.chapters} "
            f"chapter{'s' if canonical.chapters != 1 else ''}; "
            f"chapter {chapter_number} does not exist.",
        )

    start = 1 if verse_start is None else _positive_int(verse_start, "Starting verse")
    end = start if verse_end is None else _positive_int(verse_end, "Ending verse")
    if end < start:
        raise PassageError(422, "The verse range must start at or before its end.")

    max_verses = config.MAX_VERSES_PER_REFERENCE
    if end - start + 1 > max_verses:
        raise PassageError(422, f"At most {max_verses} verses can be requested at once.")

    if not lookup.is_loaded():
        raise PassageError(503, "Bible text is not available on this server.")

    # Validate both bounds so a request cannot silently display a different
    # passage than the one the operator chose (e.g. Genesis 1:1-999).
    for verse in {start, end}:
        if not lookup.validate_reference(canonical.name, chapter_number, verse):
            raise PassageError(
                404,
                f"{canonical.name} {chapter_number}:{verse} is not in the Bible text "
                "available on this server.",
            )

    verses = tuple(lookup.get_verse_range(canonical.name, chapter_number, start, end))
    if not verses:
        raise PassageError(
            404,
            f"No text found for {canonical.name} {chapter_number}:{start}"
            + (f"-{end}" if end != start else "")
            + ".",
        )

    return ResolvedPassage(
        book=canonical.name,
        chapter=chapter_number,
        verse_start=start,
        verse_end=end,
        verses=verses,
    )
