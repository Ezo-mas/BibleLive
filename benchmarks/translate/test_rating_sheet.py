from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rating_sheet import build_rows, deblind, key_name, score, validate  # noqa: E402

SERMON: list[dict] = [
    {
        "id": f"w-{i:03d}",
        "window_id": "w",
        "sentence_no": i,
        "clean": f"lt {i}",
        "recognized": f"rec {i}",
    }
    for i in range(1, 6)
]
OUTPUTS = {
    "recognized": {
        "B": {s["id"]: f"b {s['id']}" for s in SERMON},
        "C": {s["id"]: f"c {s['id']}" for s in SERMON},
        "E": {s["id"]: f"b {s['id']}" if s["sentence_no"] % 2 else f"e {s['id']}" for s in SERMON},
    }
}


def test_shuffle_and_deblind_are_lossless():
    rows, key = build_rows(SERMON, OUTPUTS, "ru", random.Random(1))
    pairs = deblind(rows, key)
    got = {(letter, r["item_id"], r["translation"]) for letter, r in pairs}
    want = {(letter, i, t) for letter, m in OUTPUTS["recognized"].items() for i, t in m.items()}
    assert got == want
    assert len(pairs) == 15  # every system/sentence pair, once


def test_identical_translations_appear_once():
    rows, _ = build_rows(SERMON, OUTPUTS, "ru", random.Random(1))
    odd = [r for r in rows if r["item_id"] == "w-001"]
    even = [r for r in rows if r["item_id"] == "w-002"]
    assert len(odd) == 2 and len(even) == 3


def test_codes_do_not_reveal_systems():
    rows, key = build_rows(SERMON, OUTPUTS, "ru", random.Random(7))
    letters_for_code: dict[str, set[str]] = {}
    for codes in key.values():
        for code, letters in codes.items():
            letters_for_code.setdefault(code, set()).update(letters)
    # the same code stands for different systems on different sentences
    assert any(len(v) > 1 for v in letters_for_code.values())


def test_reuse_skips_rated_rows():
    rated = {("w-001", "recognized", "b w-001")}
    rows, _ = build_rows(SERMON, OUTPUTS, "ru", random.Random(1), rated)
    assert [r["translation"] for r in rows if r["item_id"] == "w-001"] == ["c w-001"]


def test_validate_and_score():
    rows, key = build_rows(SERMON[:1], OUTPUTS, "ru", random.Random(1))
    assert validate(rows)  # unfilled
    for r in rows:
        r.update(adequacy="3", critical="0", critical_type="", fluency="2")
    rows[0].update(adequacy="0", critical="1", critical_type="meaning")
    assert validate(rows) == []
    result = {(s["system"], s["version"]): s for s in score(deblind(rows, key))}
    assert sum(s["rated"] for s in result.values()) == 3
    crit = [s for s in result.values() if s["critical_share"] > 0]
    assert crit and all(s["critical_by_type"] == {"meaning": 1} for s in crit)


def test_key_name_finds_the_built_sheet():
    keys: dict[str, dict] = {"stage1-ru": {}, "stage2-ru": {}}
    assert key_name("stage2-ru", keys) == "stage2-ru"
    assert key_name("sol-stage1-ru-reconciled", keys) == "stage1-ru"
