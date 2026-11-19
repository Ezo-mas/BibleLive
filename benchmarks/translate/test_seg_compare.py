from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seg_compare import build_units, cut_words, join_groups, join_text  # noqa: E402


def seg(t0: float, t1: float, text: str = "x") -> dict:
    return {"t0": t0, "t1": t1, "text": text}


def test_units_merge_default_cuts_without_a_t06_cut():
    default = [seg(0, 4), seg(5, 9), seg(11, 14)]
    # t06 cuts at 4-5 (shared) and 6-6.5 (inside a default segment), none at 9-11
    t06 = [seg(0, 4), seg(5, 6), seg(6.5, 14)]
    lines = [(0.0, 4.0, "a b"), (5.0, 14.0, "c d e f")]
    units = build_units(default, t06, lines)
    assert [len(u["default"]) for u in units] == [1, 2]
    assert [len(u["t06"]) for u in units] == [1, 2]
    assert [u["ref"] for u in units] == [["a", "b"], ["c", "d", "e", "f"]]


def test_skip_line_marks_its_unit():
    units = build_units([seg(0, 4), seg(6, 9)], [seg(0, 4), seg(6, 9)], [(5.5, 9.5, "<skip>")])
    assert [u["skip"] for u in units] == [False, True]


def test_cut_words_goes_to_nearest_segment():
    words = [("a", 0.5), ("b", 2.2), ("c", 2.8), ("d", 4.0)]
    assert cut_words(words, [seg(0, 2), seg(3, 5)]) == ["a b", "c d"]
    assert cut_words(words, [seg(0, 5)]) == ["a b c d"]


def test_join_groups_until_sentence_end_with_min_span_and_cap():
    segs = [seg(0, 2, "a"), seg(2.5, 3, "b."), seg(3.5, 6, "c."), seg(6.5, 7, "d")]
    assert join_groups(segs, 0.0) == [[0, 1], [2], [3]]
    assert join_groups(segs, 4.0) == [[0, 1, 2], [3]]
    long = [seg(0, 6, "a"), seg(6.5, 11, "b"), seg(11.5, 13, "c.")]
    assert join_groups(long, 0.0) == [[0, 1], [2]]  # 13 s would pass the 12 s cap


def test_join_text_depunctuates_the_joint():
    assert (
        join_text(["Kartą jis atėjo.", "Ir apgulė."], depunct=True) == "Kartą jis atėjo ir apgulė."
    )
    assert (
        join_text(["Kartą jis atėjo.", "Ir apgulė."], depunct=False)
        == "Kartą jis atėjo. Ir apgulė."
    )
    assert join_text(["Na...", "Gerai."], depunct=True) == "Na... Gerai."
