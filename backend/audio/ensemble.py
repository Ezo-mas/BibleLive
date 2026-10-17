"""Two-model agreement: merge the references two speech models found in one utterance.

In two-model mode (``--stt kmynas+canary``) every utterance is transcribed by
both models and each transcript goes through its own parser. A reference both
found keeps its confidence; one only a single model found still reaches the
operator, but as "low", so auto-approve never shows it unseen. On the benchmark
corpus both models found the same reference in the same utterance 149 times
out of 150, so agreement is judged per utterance, without holding references
back for a later one.
"""

from core.verse_parser import Reference

_RANK = {"low": 0, "medium": 1, "high": 2}


def reference_key(ref: Reference) -> tuple:
    """What two models must both report for a reference to count as agreed."""
    return (ref.book, ref.chapter, ref.verse_start, ref.verse_end)


def merge_agreement(primary: list[Reference], alternate: list[Reference]) -> list[Reference]:
    """One list of references from two models' parses of the same utterance.

    Each reference appears once, primary model's first. An agreed reference
    takes the higher of the two confidences; a single-model one becomes "low".
    The returned objects are the parsers' own, updated in place.
    """
    alternate_by_key: dict[tuple, Reference] = {}
    for ref in alternate:
        alternate_by_key.setdefault(reference_key(ref), ref)

    merged: list[Reference] = []
    seen: set[tuple] = set()
    for ref in primary:
        key = reference_key(ref)
        if key in seen:
            continue
        seen.add(key)
        other = alternate_by_key.get(key)
        if other is None:
            ref.confidence = "low"
        elif _RANK.get(other.confidence, 0) > _RANK.get(ref.confidence, 0):
            ref.confidence = other.confidence
        merged.append(ref)
    for key, ref in alternate_by_key.items():
        if key not in seen:
            seen.add(key)
            ref.confidence = "low"
            merged.append(ref)
    return merged
