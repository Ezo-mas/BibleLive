"""Replay a live session log through this checkout's reference parser.

A session log (``--session-log-dir``; the launcher writes them to
``logs/sessions`` in its data directory) keeps every utterance's transcripts and
the references the live pipeline sent for it. This feeds those transcripts
through the detector the pipeline itself builds (per-model
spelling maps, duplicate collapse, low-confidence marking, two-model agreement,
context reset on a language switch) and prints what the current code finds next
to what was sent live. Run it before and after a parser change to see exactly
which references of a real service change.

Usage:
    python benchmarks/session_replay.py <session.jsonl> [--diff-only]
        [--bible-db bible_data/biblelive.sqlite]
        [--verse-text-db bible_data/bible_ltb.sqlite]

From a worktree, pass the main checkout's databases (bible_data/ is not in git).
One difference from live: the parser's context expiry is measured on utterance
start times in the recording, where live uses the wall clock.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from audio.detector import UtteranceDetector  # noqa: E402
from audio.metrics import PipelineMetrics  # noqa: E402
from audio.stt import Transcription  # noqa: E402
from core.bible_lookup import BibleLookup  # noqa: E402
from core.verse_parser import Reference  # noqa: E402
from core.verse_text import VerseTextIndex  # noqa: E402


def label(ref: Reference) -> str:
    return f"{ref} [{ref.confidence}]"


def logged_labels(record: dict[str, Any]) -> list[str]:
    """The references the live pipeline sent for one utterance record."""
    return [f"{r['reference']} [{r['confidence']}]" for r in record.get("references") or []]


class Replayer:
    """The live pipeline's parsing steps for logged utterances, in order."""

    def __init__(self, validator=None, verse_text: VerseTextIndex | None = None):
        self.detector = UtteranceDetector(
            [], validator, verse_text, languages=(), metrics=PipelineMetrics()
        )
        self._last_t: float | None = None

    @staticmethod
    def _transcription(
        transcript: dict[str, Any], alternate: Transcription | None = None
    ) -> Transcription:
        return Transcription(
            text=transcript.get("text"),
            duration_ms=0.0,
            low_confidence_reason=transcript.get("low_confidence_reason"),
            source=transcript["model"],
            alternate=alternate,
        )

    def feed(self, record: dict[str, Any]) -> tuple[list[Reference], list[Reference]]:
        """(references flushed by a language switch, the utterance's references)."""
        transcripts = record.get("transcripts") or []
        if not transcripts:
            return [], []
        primary = transcripts[0]
        alternate = transcripts[1] if len(transcripts) > 1 else None
        if primary.get("text") is None and (alternate is None or alternate.get("text") is None):
            return [], []
        language = record["language"]
        if record.get("start_s") is not None:
            self._last_t = record["start_s"]
        detector = self.detector
        detector.add_language(language, [t["model"] for t in transcripts[:2]])
        alternate_result = self._transcription(alternate) if alternate is not None else None
        result = self._transcription(primary, alternate_result)
        detected = detector.detect(result, language, self._last_t)
        return detected.released, detected.references or []

    def finish(self) -> list[Reference]:
        """References still held at the end of the session."""
        language = self.detector.last_parser_language
        return self.detector.flush_held(language) if language is not None else []


def replay(lines, replayer: Replayer, diff_only: bool = False) -> tuple[list[str], int, int]:
    """Output lines, the number of utterances compared, and how many differ from live."""
    out: list[str] = []
    compared = differ = 0
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("type") != "utterance":
            continue
        flushed, refs = replayer.feed(record)
        when = f"{record.get('time', '?')} {record.get('start_s') or 0:8.1f}s"
        out += [f"{when}  flushed on language switch: {label(r)}" for r in flushed]
        now, live = [label(r) for r in refs], logged_labels(record)
        if not now and not live:
            continue
        compared += 1
        same = now == live
        differ += not same
        if diff_only and same:
            continue
        line_out = f"{when}  {'same' if same else 'DIFF'}  now: {', '.join(now) or '-'}"
        out.append(line_out if same else f"{line_out}  | live: {', '.join(live) or '-'}")
    out += [f"end of session, still held: {label(r)}" for r in replayer.finish()]
    return out, compared, differ


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("session", help="session .jsonl file")
    ap.add_argument("--bible-db", default=str(REPO_ROOT / "bible_data" / "biblelive.sqlite"))
    ap.add_argument("--verse-text-db", default=str(REPO_ROOT / "bible_data" / "bible_ltb.sqlite"))
    ap.add_argument("--diff-only", action="store_true", help="print only utterances that differ")
    args = ap.parse_args(argv)

    validator = None
    if Path(args.bible_db).is_file():
        lookup = BibleLookup(args.bible_db, readonly=True)
        validator = lookup.get_validator_hook() if lookup.conn else None
    else:
        print(f"[warn] {args.bible_db} not found: non-existent verses are kept", file=sys.stderr)
    verse_text = None
    if Path(args.verse_text_db).is_file():
        verse_text = VerseTextIndex.from_sqlite(args.verse_text_db)
    else:
        print(f"[warn] {args.verse_text_db} not found: chapter-only refs stay so", file=sys.stderr)

    with open(args.session, encoding="utf-8") as f:
        out, compared, differ = replay(f, Replayer(validator, verse_text), args.diff_only)
    print("\n".join(out))
    print(f"{compared} utterance(s) with references; {differ} differ from what was sent live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
