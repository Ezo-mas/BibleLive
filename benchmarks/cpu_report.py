"""Summarize CPU benchmark runs: speed, dense-speech tail, throttling, sanity score.

Reads result JSONs written by run_benchmark.py in windowed mode (live runs, which
carry per-window timings) and prints one row per model.

    python benchmarks/cpu_report.py results/laptop-x/small.json results/laptop-x/base.json
    python benchmarks/cpu_report.py results/laptop-x/*.json --clock results/laptop-x/clock.csv

Speed is RTF = seconds of compute per second of audio transcribed. With ``--hop-s``
equal to ``--window-s`` (non-overlapping windows, which the laptop script uses)
every audio second is transcribed once, which matches what production does per
utterance; production also skips silence, so it is at most this expensive. A run
with overlapping windows transcribes the audio twice and would read about 2x slow.

Per-window time follows how much is said, not only how hot the CPU is (a quiet
window can take 3 s and a dense one 36 s), so the report does NOT infer throttling
from early-vs-late windows. Throttling comes from the sampled CPU clock instead.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

# A live service must keep up with its audio; leave headroom for OBS or a browser
# sharing the machine.
TARGET_RTF = 0.5
# Up to 1.0 the machine keeps up on average but dense passages lag behind real time.
LIMIT_RTF = 1.0
# A clock-speed drop larger than this between the start and end of the run means
# the CPU is throttling (heat or power limits).
CLOCK_DROP_WARN = 0.10


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile, ``q`` in [0, 1]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


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
        "rtf_p90": None,
        "rtf_max": None,
    }
    if times:
        row["rtf"] = mean(times) / window_s
        row["rtf_p90"] = percentile(times, 0.9) / window_s
        row["rtf_max"] = max(times) / window_s
    return row


def verdict(row: dict[str, Any]) -> str:
    if row["rtf"] is None:
        return "no per-window timings (cached replay? re-run with --no-cache)"
    if row["rtf"] <= TARGET_RTF:
        text = "keeps up"
    elif row["rtf"] <= LIMIT_RTF:
        text = f"marginal: average RTF above {TARGET_RTF}, little headroom"
    else:
        text = "TOO SLOW: cannot keep up with real time"
    if row["rtf_p90"] is not None and row["rtf_p90"] > LIMIT_RTF:
        text += "; dense speech runs slower than real time (queue builds, then drains in pauses)"
    return text


def load_clock(path: Path) -> list[tuple[float, float]]:
    """Read ``epoch,mhz,temp_c`` samples; rows without a clock reading are skipped."""
    samples: list[tuple[float, float]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            try:
                samples.append((float(row[1]), float(row[2]) if row[2] else 0.0))
            except (IndexError, ValueError):
                continue
    return samples


def clock_summary(samples: list[tuple[float, float]]) -> str:
    """Compare the clock speed in the first and last third of the busy samples."""
    if len(samples) < 6:
        return "clock: too few samples to judge throttling"
    k = len(samples) // 3
    first = mean([mhz for mhz, _ in samples[:k]])
    last = mean([mhz for mhz, _ in samples[-k:]])
    temp = max(t for _, t in samples)
    drop = (first - last) / first if first > 0 else 0.0
    line = f"clock: {first:.0f} MHz early -> {last:.0f} MHz late ({-drop:+.0%}), peak {temp:.0f} C"
    if drop > CLOCK_DROP_WARN:
        line += "  THROTTLING: sustained speed is lower than the early speed"
    return line


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


def render(rows: list[dict[str, Any]], clock: str | None = None) -> str:
    def f(x: float | None) -> str:
        return "-" if x is None else f"{x:.2f}"

    header = f"{'model':8} {'rec':14} {'win':>4} {'RTF':>5} {'p90':>5} {'max':>5} {'TP/FP/FN':>9}  verdict"
    lines = [header, "-" * len(header)]
    for r in rows:
        score = f"{r['tp']}/{r['fp']}/{r['fn']}"
        lines.append(
            f"{str(r['model']):8} {r['record_id'][:14]:14} {r['windows']:>4} {f(r['rtf']):>5} "
            f"{f(r['rtf_p90']):>5} {f(r['rtf_max']):>5} {score:>9}  {verdict(r)}"
        )
    lines.append("RTF = mean; p90/max = the slowest windows (dense speech).")
    if clock:
        lines.append(clock)
    if any(r["overlap"] for r in rows):
        lines.append(
            "WARNING: a run used overlapping windows (hop < window); its RTF counts every "
            "audio second twice. Re-run with --hop-s equal to --window-s."
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results", nargs="+", type=Path)
    ap.add_argument("--clock", type=Path, help="CSV of epoch,mhz,temp_c samples taken during runs")
    args = ap.parse_args()
    clock = None
    if args.clock and args.clock.is_file():
        clock = clock_summary(load_clock(args.clock))
    print(render(load_rows(args.results), clock))
    return 0


if __name__ == "__main__":
    sys.exit(main())
