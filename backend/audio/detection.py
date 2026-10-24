"""Detection wiring shared by the live pipeline and the benchmark.

The benchmark is the acceptance gate for every detection change, so it must
build parsers, collapse duplicate references, mark a shaky decode "low", reset
context on a language switch and start Whisper exactly as the live pipeline
does. Each of those rules lives here once; ``tests/test_detection_parity.py``
drives both sides and compares them.

What stays at the call sites on purpose: live turns a parser exception into
"no references" and keeps metrics and logs; the benchmark lets a parser error
raise and also dedupes across a whole feed group.
"""

from __future__ import annotations

from collections.abc import Callable

from core.verse_parser import Reference, ReferenceParser, StreamingParser
from core.verse_text import VerseTextIndex

from .ensemble import merge_agreement
from .spelling_maps import MappedStreamingParser, spelling_map
from .stt import SUPPORTED_LANGUAGES
from .stt_gguf import GGUF_LANGUAGES

# Max age (s) of a parser's book/chapter context; StreamingParser's default.
CONTEXT_TTL_S = 600.0


def parser_sources(models: list[str], language: str) -> list[str]:
    """Whose transcripts ``language``'s parsers read, first model first.

    ``models`` are the engine's GGUF models (``engine_models``; [] for Whisper).
    They transcribe their languages only, so other languages' parsers read
    Whisper. Two names means two-model mode: one parser per model.
    """
    return (models if language in GGUF_LANGUAGES else []) or ["whisper"]


def build_streaming_parser(
    language: str,
    validator: Callable[[Reference], bool] | None,
    verse_text: VerseTextIndex | None,
    source: str,
    context_ttl_s: float | None = CONTEXT_TTL_S,
) -> StreamingParser:
    """One streaming parser for ``source``'s transcripts in ``language``."""
    parser = ReferenceParser(validator=validator, language=language)
    lt_text = verse_text if language == "lt" else None
    rewrite = spelling_map(source, language)
    # Hold a chapter-only ref one utterance in case the verse follows.
    if rewrite is not None:
        return MappedStreamingParser(
            parser,
            language=language,
            context_ttl_s=context_ttl_s,
            hold_chapter_only=True,
            verse_text=lt_text,
            spelling_map=rewrite,
        )
    return StreamingParser(
        parser,
        language=language,
        context_ttl_s=context_ttl_s,
        hold_chapter_only=True,
        verse_text=lt_text,
    )


def unique_references(
    references: list[Reference], low_confidence_reason: str | None
) -> tuple[list[Reference], int]:
    """One model's references for an utterance, each once, and how many repeats were dropped.

    One utterance can legitimately mention the same book/chapter more than once
    ("Romans 8:28 ... Romans 8:28"), but a transcript that yields the same
    reference dozens of times is a decoder artifact. A shaky decode (probably
    silence, unsure tokens, or a trimmed loop) is exactly where prompt-echo
    hallucinations come from, so with ``low_confidence_reason`` set every
    reference is marked "low": it still reaches the operator, but auto-approve
    won't show it.
    """
    unique: list[Reference] = []
    seen: set[tuple] = set()
    for ref in references:
        key = (ref.book, ref.chapter, ref.verse_start, ref.verse_end)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    if low_confidence_reason:
        for ref in unique:
            ref.confidence = "low"
    return unique, len(references) - len(unique)


def flush_held(
    parsers: dict[str, StreamingParser],
    alternates: dict[str, StreamingParser],
    language: str,
) -> list[Reference]:
    """Release ``language``'s held references (both models' in two-model mode)."""
    parser = parsers.get(language)
    held = parser.flush() if parser is not None else []
    alternate = alternates.get(language)
    if alternate is None:
        return held
    return merge_agreement(held, alternate.flush())


def switch_language(
    parsers: dict[str, StreamingParser],
    alternates: dict[str, StreamingParser],
    previous: str | None,
    language: str,
) -> list[Reference]:
    """Start parsing ``language`` after ``previous``: the references this releases.

    A held chapter-only reference waits one utterance for its verse. A switch
    means that verse will never come in the old language, so the previous
    language's held references are released instead of being dropped by a
    later reset. The new language's own context (carry tokens, inferred
    chapter/verse) is from before the switch, so it is flushed and reset too.
    """
    released = flush_held(parsers, alternates, previous) if previous is not None else []
    released += flush_held(parsers, alternates, language)
    for parser in (parsers.get(language), alternates.get(language)):
        if parser is not None:
            parser.reset()
    return released


def whisper_start_language(models: list[str], language: str) -> str:
    """The language Whisper starts in next to GGUF ``models`` when ``language`` is configured.

    Next to GGUF models Whisper only hears the languages they cannot, so it
    starts in one of those (its load log named Lithuanian before).
    """
    if models and language in GGUF_LANGUAGES:
        return next(lang for lang in SUPPORTED_LANGUAGES if lang not in GGUF_LANGUAGES)
    return language
