"""Phase 0, P0.5: summarize a phone spike run's events.jsonl, one line per phone setup.

    python benchmarks/translate/phone_spike/phone_spike_summary.py <run-name | path to events.jsonl>

Per client (phone + player + codec): clips sent while it was connected, received,
played, missed, the longest silence between two clip starts, and how much was
played while the screen was off (page hidden): clips sent during hidden time
against clips that started playing then. A player that keeps going with the
screen locked plays about as many as were sent.

Phone clocks are not synced with the PC; hidden-time counts use the phone's
clock for its own events and the PC's for "sent", so expect +/-1 at the edges.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from phone_spike_clips import RESULTS

LOG_ROOT = RESULTS / "translate" / "phone_spike" / "logs"


def hidden_spans(events: list[dict[str, Any]], end: float) -> list[tuple[float, float]]:
    """[start, end) spans where the page reported itself hidden, by client time."""
    spans: list[tuple[float, float]] = []
    since: float | None = None
    for e in events:
        if e.get("ev") != "visibility":
            continue
        t = float(e.get("ct", e["t"]))
        if e.get("state") == "hidden" and since is None:
            since = t
        elif e.get("state") == "visible" and since is not None:
            spans.append((since, t))
            since = None
    if since is not None:
        spans.append((since, end))
    return spans


def in_spans(t: float, spans: list[tuple[float, float]]) -> bool:
    return any(a <= t < b for a, b in spans)


def summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sent = [r for r in records if r.get("ev") == "sent"]
    by_client: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if "client" in r:
            by_client[str(r["client"])].append(r)
    out = []
    for client, evs in by_client.items():
        evs.sort(key=lambda e: float(e.get("ct", e["t"])))
        setup = next((e.get("setup") for e in evs if e.get("ev") == "connect"), None) or {}
        start = float(evs[0].get("ct", evs[0]["t"]))
        end = float(evs[-1].get("ct", evs[-1]["t"]))
        plays = [float(e["ct"]) for e in evs if e.get("ev") == "play_start" and "ct" in e]
        spans = hidden_spans(evs, end)
        sent_in_run = [float(s["t"]) for s in sent if start <= float(s["t"]) <= end]
        longest_gap = max((b - a for a, b in zip(plays, plays[1:], strict=False)), default=0.0)
        problems = sorted({str(e["ev"]) for e in evs if str(e.get("ev", "")).endswith(
            ("_error", "_rejected", "unsupported", "paused", "session_pause"))})  # fmt: skip
        out.append({
            "client": client,
            "player": setup.get("player"), "codec": setup.get("codec"),
            "audio_session": setup.get("audioSession"),
            "minutes": round((end - start) / 60, 1),
            "sent": len(sent_in_run),
            "received": sum(1 for e in evs if e.get("ev") == "rx"),
            "played": len(plays),
            "missed": sum(int(e.get("n", 0)) for e in evs if e.get("ev") == "missed"),
            "longest_gap_s": round(longest_gap, 1),
            "hidden_min": round(sum(b - a for a, b in spans) / 60, 1),
            "sent_hidden": sum(1 for t in sent_in_run if in_spans(t, spans)),
            "played_hidden": sum(1 for t in plays if in_spans(t, spans)),
            "disconnects": sum(1 for e in evs if e.get("ev") == "disconnect"),
            "problems": problems,
        })  # fmt: skip
    return out


def load(arg: str) -> list[dict[str, Any]]:
    path = Path(arg)
    if not path.exists():
        path = LOG_ROOT / arg / "events.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run")
    args = ap.parse_args()
    for row in summarize(load(args.run)):
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
