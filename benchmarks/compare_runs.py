"""Compare detection-benchmark runs by tag: per recording and in total.

A run is every result JSON written by ``run_benchmark.py`` whose name is
``<prefix>-<tag>.json`` or ``<prefix>-<tag>-<record id>.json`` (one file per
recording, as parallel runs write them). False positives are split by
confidence: low ones always wait for the operator, medium and high ones can be
auto-approved onto the screen, so those are the ones that matter most.

Usage:
    python benchmarks/compare_runs.py <tag> [<tag> ...]
        [--results-dir results] [--prefix review5span]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PREFIX = "review5span"


@dataclass
class RecordingScore:
    tp: int
    fn: int
    fp_low: int
    fp_med_high: int
    runtime_s: float
    rtf: float
    cached: bool
    failures: int
    repeats: int = 0  # citations said again: correct, not FPs


def score(per_recording: dict[str, Any]) -> RecordingScore:
    """One recording's entry from a result JSON's ``aggregate.per_recording``."""
    fp_med_high = sum(fp.get("confidence") != "low" for fp in per_recording["false_positives"])
    return RecordingScore(
        tp=per_recording["tp"],
        fn=per_recording["fn"],
        fp_low=per_recording["fp"] - fp_med_high,
        fp_med_high=fp_med_high,
        runtime_s=per_recording["runtime_s"],
        rtf=per_recording["rtf"],
        cached=bool(per_recording.get("config", {}).get("transcripts_cached")),
        failures=len(per_recording.get("failures") or []),
        repeats=len(per_recording.get("repeats") or []),
    )


def load_run(
    results_dir: Path, tag: str, prefix: str = DEFAULT_PREFIX
) -> dict[str, RecordingScore]:
    """Every recording of run ``tag``, keyed by record id.

    A file counts only when its name is exactly the tag's (optionally followed by
    one of the record ids inside it), so tag ``cut6-k`` never picks up ``cut6-kcv``.
    """
    run: dict[str, RecordingScore] = {}
    stem = f"{prefix}-{tag}"
    for path in sorted(results_dir.glob(f"{stem}*.json")):
        per_recording = json.loads(path.read_text(encoding="utf-8"))["aggregate"]["per_recording"]
        if path.stem != stem and path.stem not in {f"{stem}-{rid}" for rid in per_recording}:
            continue
        for rid, entry in per_recording.items():
            run[rid] = score(entry)
    return run


def render(runs: dict[str, dict[str, RecordingScore]]) -> str:
    """A per-recording table (TP / medium+high FP) and one totals line per tag."""
    tags = list(runs)
    records = sorted({rid for run in runs.values() for rid in run})
    width = max([len("recording"), *map(len, records)])
    lines = [f"{'recording':<{width}}" + "".join(f"  {tag:>16}" for tag in tags)]
    for rid in records:
        cells = []
        for tag in tags:
            s = runs[tag].get(rid)
            cells.append(f"  {'missing' if s is None else f'{s.tp} / {s.fp_med_high}':>16}")
        lines.append(f"{rid:<{width}}" + "".join(cells))
    lines.append(f"(cells: TP / medium+high FP; {len(records)} recordings)")
    for tag in tags:
        scores = list(runs[tag].values())
        cached = sum(s.cached for s in scores)
        runtime = sum(s.runtime_s for s in scores)
        timing = (
            f"runtime {runtime:.0f}s"
            if cached == 0
            else f"runtime {runtime:.0f}s ({cached}/{len(scores)} replayed from cache, not a speed figure)"
        )
        missing = len(records) - len(scores)
        lines.append(
            f"{tag}: TP {sum(s.tp for s in scores)}  FN {sum(s.fn for s in scores)}"
            f"  FP low {sum(s.fp_low for s in scores)}"
            f"  FP med+high {sum(s.fp_med_high for s in scores)}"
            f"  repeats {sum(s.repeats for s in scores)}  {timing}"
            + (
                f"  failures {sum(s.failures for s in scores)}"
                if any(s.failures for s in scores)
                else ""
            )
            + (f"  MISSING {missing} recording(s)" if missing else "")
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = ap.parse_args(argv)
    runs = {tag: load_run(Path(args.results_dir), tag, args.prefix) for tag in args.tags}
    empty = [tag for tag, run in runs.items() if not run]
    if empty:
        print(f"error: no results for {', '.join(empty)} in {args.results_dir}", file=sys.stderr)
        return 2
    print(render(runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
