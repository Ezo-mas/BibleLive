"""Two-model agreement: merge the references two speech models found in one utterance.

In two-model mode (``--stt kmynas+canary``) every utterance is transcribed by
both models and each transcript goes through its own parser. A reference both
found keeps its confidence; one only a single model found still reaches the
operator, but as "low", so auto-approve never shows it unseen. On the benchmark
corpus both models found the same reference in the same utterance 149 times
out of 150, so agreement is judged per utterance, without holding references
back for a later one.

Agreement compares parsed references, never transcript text. One allowance:
a range and its start verse agree ("Psalms 40:1-2" from one model, "Psalms
40:1" from the other, which dropped the second number), and the range is
kept.
"""

from core.verse_parser import Reference

_RANK = {"low": 0, "medium": 1, "high": 2}


def reference_key(ref: Reference) -> tuple:
    """What two models must both report for a reference to count as agreed."""
    return (ref.book, ref.chapter, ref.verse_start, ref.verse_end)


def _start_key(ref: Reference) -> tuple | None:
    """Book, chapter and first verse; None for a chapter-only reference."""
    if ref.verse_start is None:
        return None
    return (ref.book, ref.chapter, ref.verse_start)


def _range_agrees(a: Reference, b: Reference) -> bool:
    """A range and its own start verse, as one model may drop the range's end."""
    return (
        _start_key(a) is not None
        and _start_key(a) == _start_key(b)
        and (a.verse_end is None) != (b.verse_end is None)
    )


def merge_agreement(primary: list[Reference], alternate: list[Reference]) -> list[Reference]:
    """One list of references from two models' parses of the same utterance.

    Each reference appears once, primary model's first. An agreed reference
    takes the higher of the two confidences (for a range against its start
    verse, the range is kept); a single-model one becomes "low". The returned
    objects are the parsers' own, updated in place.
    """
    primary_unique: dict[tuple, Reference] = {}
    for ref in primary:
        primary_unique.setdefault(reference_key(ref), ref)
    alternate_unique: dict[tuple, Reference] = {}
    for ref in alternate:
        alternate_unique.setdefault(reference_key(ref), ref)

    # Pair each primary reference with the alternate one it agrees with:
    # the same reference first, then a range against its start verse.
    pairs: dict[tuple, tuple] = {}
    unpaired = {key for key in alternate_unique if key not in primary_unique}
    for key, ref in primary_unique.items():
        if key in alternate_unique:
            pairs[key] = key
            continue
        for other_key in sorted(unpaired, key=str):
            if _range_agrees(ref, alternate_unique[other_key]):
                pairs[key] = other_key
                unpaired.discard(other_key)
                break

    merged: list[Reference] = []
    for key, ref in primary_unique.items():
        match_key = pairs.get(key)
        if match_key is None:
            ref.confidence = "low"
            merged.append(ref)
            continue
        other = alternate_unique[match_key]
        kept = other if ref.verse_end is None and other.verse_end is not None else ref
        best = max(ref.confidence, other.confidence, key=lambda c: _RANK.get(c, 0))
        kept.confidence = best
        merged.append(kept)
    for key in alternate_unique:
        if key in unpaired:
            ref = alternate_unique[key]
            ref.confidence = "low"
            merged.append(ref)
    return merged
