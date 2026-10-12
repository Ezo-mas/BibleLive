"""Summarize CPU benchmark runs: speed, throttling drift, and a sanity score.

Reads result JSONs written by run_benchmark.py in windowed mode (live runs, which
carry per-window timings) and prints one row per model.

    python benchmarks/cpu_report.py results/laptop-x/small.json results/laptop-x/base.json

Speed is reported as RTF = seconds of compute per second of audio transcribed.
With ``--hop-s`` equal to ``--window-s`` (non-overlapping windows, which the
laptop script uses) every audio second is transcribed once, which matches what
production does per utterance; production also skips silence, so it is at most
this expensive. A run with overlapping windows transcribes the audio twice and
would read about 2x too slow.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# A live service must keep up with its audio; leave headroom for silence-free
# stretches and for OBS or the browser sharing the machine.
TARGET_RTF = 0.5
# Drift above this between the first and last third means the machine is slowing
# down as it heats up (or another load started), so the early number flatters it.
DRIFT_WARN = 0.15


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def thirds(times: list[float]) -> tuple[float, float]:
    """Mean per-window time of the first and last third (needs >= 3 windows)."""
    n = len(times)
    if n < 3:
        raise ValueError("need at least 3 windows to compare thirds")
    k = n // 3
    return mean(times[:k]), mean(times[-k:])


def drift(first: float, last: float) -> float:
    """Relative slowdown from the first to the last third (0.20 = 20% slower)."""
    return (last - first) / first if first > 0 else 0.0


def summarize_recording(rec: dict[str, Any], window_s: float) -> dict[str, Any]:
    times = rec.get("config", {}).get("window_times_s") or []
    row: dict[str, Any] = {
        "record_id": rec["record_id"],
        "model": rec["config"].get("whisper_model"),
        "windows": len(times),
        "tp": rec["tp"],
        "fp": rec["fp"],
        "fn": rec["fn"],
        "rtf": None,
        "rtf_first": None,
        "rtf_last": None,
        "drift": None,
    }
    if times:
        row["rtf"] = mean(times) / window_s
    if len(times) >= 3:
        first, last = thirds(times)
        row["rtf_first"], row["rtf_last"] = first / window_s, last / window_s
        row["drift"] = drift(first, last)
    return row


def verdict(row: dict[str, Any]) -> str:
    if row["rtf"] is None:
        return "no per-window timings (cached replay? re-run with --no-cache)"
    sustained = row["rtf_last"] if row["rtf_last"] is not None else row["rtf"]
    notes = []
    notes.append("keeps up" if sustained <= TARGET_RTF else f"TOO SLOW (target <= {TARGET_RTF})")
    if row["drift"] is not None and row["drift"] > DRIFT_WARN:
        notes.append(f"slows {row['drift']:.0%} as it heats up")
    return "; ".join(notes)


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        window_s = float(data.get("window_s") or 30.0)
        hop_s = float(data.get("hop_s") or window_s)
        for rec in data["recordings"]:
            row = summarize_recording(rec, window_s)
            row["overlap"] = hop_s < window_s
            row["source"] = path.name
            rows.append(row)
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    def f(x: float | None) -> str:
        return "-" if x is None else f"{x:.2f}"

    header = f"{'model':8} {'rec':14} {'win':>4} {'RTF':>5} {'first':>6} {'last':>6} {'TP/FP/FN':>9}  verdict"
    lines = [header, "-" * len(header)]
    for r in rows:
        score = f"{r['tp']}/{r['fp']}/{r['fn']}"
        lines.append(
            f"{str(r['model']):8} {r['record_id'][:14]:14} {r['windows']:>4} {f(r['rtf']):>5} "
            f"{f(r['rtf_first']):>6} {f(r['rtf_last']):>6} {score:>9}  {verdict(r)}"
        )
    if any(r["overlap"] for r in rows):
        lines.append(
            "WARNING: a run used overlapping windows (hop < window); its RTF counts every "
            "audio second twice. Re-run with --hop-s equal to --window-s."
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results", nargs="+", type=Path)
    args = ap.parse_args()
    print(render(load_rows(args.results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
