"""Utterance detection shared by the live pipeline, benchmark and session replay.

The caller owns timing, delivery and transcripts. Live and replay recover from
parser errors; the benchmark raises them. Parser state belongs to the caller's
transcription thread, including the validator and Lithuanian verse-text index.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.verse_parser import Reference, StreamingParser
from core.verse_text import VerseTextIndex

from .detection import (
    CONTEXT_TTL_S,
    build_streaming_parser,
    flush_held,
    parser_sources,
    switch_language,
    unique_references,
)
from .ensemble import merge_agreement
from .metrics import PipelineMetrics
from .stt import SUPPORTED_LANGUAGES, Transcription

# Keep the live pipeline's existing log source as well as its messages.
logger = logging.getLogger("audio.pipeline")


@dataclass
class ParserSelection:
    parser: StreamingParser | None
    released: list[Reference]
    changed: bool = False


@dataclass
class DetectionResult:
    released: list[Reference]
    # None means that parsing failed (rather than successfully finding nothing).
    references: list[Reference] | None


class UtteranceDetector:
    """Per-language, per-model detection state, without transcription or delivery.

    The caller supplies a ``validator`` and the optional ``verse_text`` index.
    Live selects once, delivers switch releases, then calls ``detect_selected``;
    ``detect`` also selects for callers that do not need separate delivery.
    """

    def __init__(
        self,
        models: list[str],
        validator: Callable[[Reference], bool] | None = None,
        verse_text: VerseTextIndex | None = None,
        *,
        languages: Iterable[str] = SUPPORTED_LANGUAGES,
        context_ttl_s: float | None = CONTEXT_TTL_S,
        metrics: PipelineMetrics | None = None,
        catch_parser_errors: bool = True,
    ):
        self.validator = validator
        self.verse_text = verse_text
        self.metrics = metrics
        self.catch_parser_errors = catch_parser_errors
        self.streaming_parsers: dict[str, StreamingParser] = {}
        self.alternate_parsers: dict[str, StreamingParser] = {}
        self.last_parser_language: str | None = None
        self.issues: list[dict[str, Any]] = []
        if validator is None:
            self.issues.append(
                {
                    "code": "bible_db_unavailable",
                    "params": {},
                    "text": "The pipeline could not open the Bible database, so detections "
                    "are not checked against it",
                }
            )
        for language in languages:
            try:
                self.add_language(language, parser_sources(models, language), context_ttl_s)
            except Exception as e:
                if not catch_parser_errors:
                    raise
                logger.error(f"Failed to initialize '{language}' parser: {e}")
                self.issues.append(
                    {
                        "code": "parser_failed",
                        "params": {"language": language},
                        "text": f"The '{language}' reference parser failed to start, so no "
                        "references are detected in that language",
                    }
                )

    _make_parser = staticmethod(build_streaming_parser)

    def add_language(
        self, language: str, sources: list[str], context_ttl_s: float | None = CONTEXT_TTL_S
    ) -> None:
        """Build missing parsers, including models discovered in a session log."""
        for parsers, source in zip(
            (self.streaming_parsers, self.alternate_parsers), sources, strict=False
        ):
            if language not in parsers:
                parsers[language] = self._make_parser(
                    language, self.validator, self.verse_text, source, context_ttl_s
                )

    @staticmethod
    def load_verse_text(path: str | None) -> VerseTextIndex | None:
        """The Lithuanian verse-text index, or None when it is not available."""
        if not path:
            return None
        if not Path(path).is_file():
            logger.info(
                "Verse text database %s not found: chapter-only references are "
                "reported without picking the verse read aloud",
                path,
            )
            return None
        try:
            return VerseTextIndex.from_sqlite(path)
        except Exception as e:
            logger.warning(f"Failed to load verse text from {path}: {e}")
            return None

    def set_validator(self, validator: Callable[[Reference], bool]) -> None:
        """Swap validation without changing held references or parser context."""
        self.validator = validator
        for parsers in (self.streaming_parsers, self.alternate_parsers):
            for parser in parsers.values():
                parser.parser.validator = validator

    def flush_held(self, language: str) -> list[Reference]:
        return flush_held(self.streaming_parsers, self.alternate_parsers, language)

    def select_parser(self, language: str, *, commit: bool = True) -> ParserSelection:
        """Select a language; live commits it only after delivering the releases.

        With ``commit=False`` the caller must set ``last_parser_language`` itself
        once the releases are delivered, or every utterance resets the context.
        """
        parser = self.streaming_parsers.get(language)
        if parser is None:
            logger.warning("No streaming parser available for language '%s'", language)
            return ParserSelection(None, [])
        if language == self.last_parser_language:
            return ParserSelection(parser, [])
        released = switch_language(
            self.streaming_parsers, self.alternate_parsers, self.last_parser_language, language
        )
        if commit:
            self.last_parser_language = language
        return ParserSelection(parser, released, changed=True)

    def detect(self, result: Transcription, language: str, now: float | None) -> DetectionResult:
        """Detect one utterance, with language-switch releases kept separate."""
        selection = self.select_parser(language)
        if selection.parser is None:
            return DetectionResult(selection.released, None)
        references = self.detect_selected(result, language, now, selection.parser)
        return DetectionResult(selection.released, references)

    def detect_selected(
        self, result: Transcription, language: str, now: float | None, parser: StreamingParser
    ) -> list[Reference] | None:
        """Parse after selection and switch delivery, without selecting or releasing again."""
        references = self._parse_transcription(parser, result, now)
        alternate = result.alternate
        if alternate is not None:
            alternate_parser = self.alternate_parsers.get(language)
            alternate_refs = (
                self._parse_transcription(alternate_parser, alternate, now)
                if alternate_parser is not None
                else None
            )
            if references is None and alternate_refs is None:
                return None
            shaky = bool(result.low_confidence_reason or alternate.low_confidence_reason)
            references = merge_agreement(
                references or [], alternate_refs or [], promote_inferred=not shaky
            )
        return references

    def _parse_transcription(
        self, parser: StreamingParser, result: Transcription, now: float | None
    ) -> list[Reference] | None:
        if result.text is None:
            return []
        try:
            references = parser.feed(result.text, now)
        except Exception as e:
            if not self.catch_parser_errors:
                raise
            logger.error(f"Error parsing references: {e}", exc_info=True)
            return None
        unique, duplicates = unique_references(references, result.low_confidence_reason)
        # Benchmark has no per-model counters or live diagnostic messages.
        if self.metrics is not None:
            if duplicates:
                self.metrics.references_deduplicated += duplicates
                logger.warning(
                    f"Collapsed {duplicates} duplicate reference(s) within this utterance"
                )
            if result.low_confidence_reason and unique:
                self.metrics.references_low_confidence += len(unique)
                logger.warning(
                    f"Low-confidence transcription ({result.low_confidence_reason}): "
                    f"marking {len(unique)} reference(s) as low confidence"
                )
        return unique
