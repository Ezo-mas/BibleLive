"""Phase 0, P0.1 step 4: select and freeze the rating sentences (sermon150).

Rule (docs/translate/phase0/RATING_GUIDE.md, fixed before anyone sees output):
from each corrected window, two runs of consecutive sentences, the first
starting at the first full sentence after 0:30 into the window and the second
after 3:00 (or after the first run, if that ends later); 13 and 12 sentences
(25 per window). Sentences containing ``(?)`` and ``<skip>`` lines are passed
over (a ``<skip>`` line also ends a sentence).

Sentences end at ``.``, ``?`` or ``!`` (closing quotes and brackets may
follow). A sentence's start time is interpolated linearly inside its line by
word position. The "as recognized" text is the raw Kmynas output
(``results/translate/recognized/``) for the same words, found by aligning
the corrected and raw word sequences of the window.

Writes, under results/translate/ (refuses to overwrite unless --force):
  sermon150.jsonl              one record per sentence, both versions
  sermon150.clean.jsonl        {"id", "run", "text"}: mt_bench.py input
  sermon150.recognized.jsonl   same, recognized text

Usage: python benchmarks/translate/build_sermon150.py [--drafts-ok] [--out-prefix sermon150]
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_transcript import check_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[2] / "results" / "translate"
WINDOW_IDS = ["1011-cite", "1011-plain", "1004-cite", "1004-plain", "0924-cite", "0924-plain"]
RUNS = ((30.0, 13), (180.0, 12))  # (offset into the window in s, sentences)
LINE_RE = re.compile(r"^\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\] (.+)$")
SENTENCE_END = re.compile(r"[.?!…][\"“”„»)\]’']*$")


@dataclass
class Word:
    text: str
    t: float  # interpolated start time, s from the source file start


@dataclass
class Sentence:
    words: list[Word]
    first_index: int  # index of its first word in the window's word list

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def start(self) -> float:
        return self.words[0].t


def parse(path: Path) -> tuple[dict[str, str], list[tuple[float, float, str]]]:
    headers, lines = {}, []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        if raw.startswith("#"):
            m = re.match(r"#\s*(\w+):\s*(.*)$", raw)
            if m:
                headers[m.group(1)] = m.group(2).strip()
            continue
        m = LINE_RE.match(raw.strip())
        if m:
            lines.append((float(m.group(1)), float(m.group(2)), m.group(3).strip()))
    return headers, lines


def words_of(lines: list[tuple[float, float, str]]) -> list[Word | None]:
    """All words in order; None marks a <skip> line (a forced sentence break)."""
    out: list[Word | None] = []
    for start, end, text in lines:
        if text == "<skip>":
            out.append(None)
            continue
        toks = text.split()
        for k, tok in enumerate(toks):
            out.append(Word(tok, start + (end - start) * k / len(toks)))
    return out


def sentences_of(words: list[Word | None]) -> list[Sentence]:
    out: list[Sentence] = []
    cur: list[Word] = []
    first = 0
    idx = 0  # index among real words (None excluded)
    for w in words:
        if w is None:
            if cur:
                out.append(Sentence(cur, first))
            cur = []
            continue
        if not cur:
            first = idx
        cur.append(w)
        idx += 1
        if SENTENCE_END.search(w.text) and not w.text.endswith("(?)"):  # (?) is not a "?"
            out.append(Sentence(cur, first))
            cur = []
    if cur:
        out.append(Sentence(cur, first))
    return out


def norm(tok: str) -> str:
    return re.sub(r"[^\w]", "", tok.lower())


def boundary_map(clean: list[str], raw: list[str]) -> list[int]:
    """For each word boundary 0..len(clean) in the corrected text, the raw index."""
    sm = difflib.SequenceMatcher(
        None, [norm(t) for t in clean], [norm(t) for t in raw], autojunk=False
    )
    m = [0] * (len(clean) + 1)
    for _tag, i1, i2, j1, j2 in sm.get_opcodes():
        for i in range(i1, i2 + 1):
            m[i] = j1 + (round((i - i1) * (j2 - j1) / (i2 - i1)) if i2 > i1 else 0)
    return m


def select(sentences: list[Sentence], window_start: float) -> list[tuple[int, Sentence]]:
    """(run number, sentence) pairs by the fixed rule."""
    chosen: list[tuple[int, Sentence]] = []
    next_pos = 0
    for run_no, (offset, count) in enumerate(RUNS, 1):
        pos = next_pos
        while pos < len(sentences) and sentences[pos].start < window_start + offset:
            pos += 1
        taken = 0
        while pos < len(sentences) and taken < count:
            s = sentences[pos]
            pos += 1
            if "(?)" in s.text:
                continue
            chosen.append((run_no, s))
            taken += 1
        next_pos = pos
    return chosen


def build(drafts_ok: bool) -> list[dict]:
    records: list[dict] = []
    for wid in WINDOW_IDS:
        corrected = ROOT / "drafts" / f"{wid}.txt"
        raw_path = ROOT / "recognized" / f"{wid}.txt"
        problems = check_text(corrected.read_text(encoding="utf-8-sig").splitlines())
        if problems and not drafts_ok:
            sys.exit(
                f"{corrected}: not finished ({len(problems)} checker problems); run check_transcript.py"
            )
        headers, lines = parse(corrected)
        window_start = float(headers["range_s"].split("-")[0])
        words = words_of(lines)
        sentences = sentences_of(words)
        clean_tokens = [w.text for w in words if w is not None]
        raw_tokens = [w.text for w in words_of(parse(raw_path)[1]) if w is not None]
        bmap = boundary_map(clean_tokens, raw_tokens)
        for no, (run_no, s) in enumerate(select(sentences, window_start), 1):
            a, b = s.first_index, s.first_index + len(s.words)
            records.append(
                {
                    "id": f"{wid}-{no:03d}",
                    "window_id": wid,
                    "run": f"{wid}-r{run_no}",
                    "sentence_no": no,
                    "start_s": round(s.start, 1),
                    "clean": s.text,
                    "recognized": " ".join(raw_tokens[bmap[a] : bmap[b]]),
                }
            )
    return records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drafts-ok", action="store_true", help="dry run on unfinished files")
    ap.add_argument("--out-prefix", default="sermon150")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    out = ROOT / f"{args.out_prefix}.jsonl"
    if out.exists() and not args.force:
        sys.exit(f"{out} is frozen; pass --force (and say why) to rebuild")
    records = build(args.drafts_ok)

    def dump(path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )

    dump(out, records)
    for version in ("clean", "recognized"):
        dump(
            ROOT / f"{args.out_prefix}.{version}.jsonl",
            [{"id": r["id"], "run": r["run"], "text": r[version]} for r in records],
        )
    per_window = {w: sum(r["window_id"] == w for r in records) for w in WINDOW_IDS}
    want = sum(n for _, n in RUNS)
    for w, n in per_window.items():
        if n < want:
            print(f"warning: {w} has {n} of {want} sentences (window ran out)")
    print(f"{len(records)} sentences -> {out}  {per_window}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
