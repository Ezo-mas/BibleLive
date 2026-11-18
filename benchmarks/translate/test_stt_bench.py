from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stt_bench import edit_distance, hyp_words, score_window  # noqa: E402


def ref(*toks: str) -> list[dict]:
    return [
        {"n": t.strip(".").lower(), "end": float(i), "sent_end": t.endswith(".")}
        for i, t in enumerate(toks)
    ]


def test_edit_distance():
    assert edit_distance(["a", "b", "c"], ["a", "x", "c"]) == 1
    assert edit_distance(["a", "b", "c"], ["a", "c"]) == 1
    assert edit_distance([], ["a", "b"]) == 2


def test_score_window_wer_boundaries_and_delay():
    r = ref("Labas", "rytas.", "Kaip", "sekasi.")
    seg = {"t0": 0.0, "t1": 3.0, "close": 4.0, "ms": 500.0, "text": "Labas rytas, kaip sekasi."}
    h = hyp_words([seg], [], s2=False)
    sc = score_window(r, h)
    assert sc["errors"] == 0
    assert (sc["tp"], sc["ref_b"], sc["hyp_b"]) == (1, 2, 1)  # missed the first sentence end
    assert sc["delays"] == [4.5, 3.5, 2.5, 1.5]  # available at close + 0.5 s


def test_skip_lines_drop_segments_centred_in_them():
    seg = {"t0": 10.0, "t1": 12.0, "close": 12.5, "ms": 100.0, "text": "giesmė"}
    assert hyp_words([seg], [(9.0, 13.0)], s2=False) == []


def test_s2_commits_the_agreed_prefix_early():
    seg = {
        "t0": 0.0, "t1": 4.0, "close": 5.0, "ms": 1000.0, "text": "vienas du trys keturi",
        "passes": [
            {"at": 1.5, "ms": 100.0, "text": "vienas du"},
            {"at": 3.0, "ms": 100.0, "text": "vienas du trys"},
        ],
    }  # fmt: skip
    at = [w["at"] for w in hyp_words([seg], [], s2=True)]
    assert at == [3.1, 3.1, 6.0, 6.0]  # two agreed after the second pass, rest at close + 1 s
