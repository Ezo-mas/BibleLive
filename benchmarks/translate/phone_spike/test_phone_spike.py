from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phone_spike_clips import interleave  # noqa: E402
from phone_spike_server import schedule_gaps  # noqa: E402
from phone_spike_summary import hidden_spans, summarize  # noqa: E402


def test_interleave_alternates_and_keeps_the_longer_tail():
    assert interleave([[1, 2, 3], ["a"]]) == [1, "a", 2, 3]


def test_schedule_has_a_long_pause_every_twelfth_clip():
    gaps = schedule_gaps(24)
    assert gaps[11] == gaps[23] == 10.0
    assert all(0.5 <= g <= 4.0 for i, g in enumerate(gaps) if (i + 1) % 12)
    assert schedule_gaps(24) == gaps  # deterministic


def test_hidden_spans_close_at_the_end_when_still_hidden():
    evs = [
        {"t": 0, "ct": 10, "ev": "visibility", "state": "hidden"},
        {"t": 0, "ct": 20, "ev": "visibility", "state": "visible"},
        {"t": 0, "ct": 30, "ev": "visibility", "state": "hidden"},
    ]
    assert hidden_spans(evs, 50) == [(10, 20), (30, 50)]


def test_summary_counts_plays_while_hidden():
    c = "iphone-element-mp3-ab12"
    records = [
        {"t": 100, "ev": "connect", "client": c, "setup": {"player": "element", "codec": "mp3"}},
        {"t": 101, "ct": 101, "ev": "start", "client": c},
        {"t": 102, "ev": "sent", "seq": 1},
        {"t": 102, "ct": 102, "ev": "rx", "seq": 1, "client": c},
        {"t": 102, "ct": 102.2, "ev": "play_start", "seq": 1, "client": c},
        {"t": 105, "ct": 105, "ev": "visibility", "state": "hidden", "client": c},
        {"t": 110, "ev": "sent", "seq": 2},
        {"t": 120, "ev": "sent", "seq": 3},
        {"t": 110, "ct": 110.3, "ev": "play_start", "seq": 2, "client": c},
        {"t": 125, "ct": 125, "ev": "missed", "n": 1, "client": c},
        {"t": 130, "ct": 130, "ev": "visibility", "state": "visible", "client": c},
    ]
    (row,) = summarize(records)
    assert row["player"] == "element"
    assert (row["sent"], row["played"], row["missed"]) == (3, 2, 1)
    assert (row["sent_hidden"], row["played_hidden"]) == (2, 1)
    assert row["longest_gap_s"] == 8.1
