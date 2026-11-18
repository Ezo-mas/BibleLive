"""Phase 0, P0.3: build blind rating sheets and score them (docs/translate/phase0/RATING_GUIDE.md).

build: one CSV per stage and target language, columns as
docs/translate/phase0/rating_sheet.template.csv. Rows for one sentence sit
together; within a sentence and version the systems are shuffled, and
identical translations from different systems appear once. The ``system``
column holds a blind code; the codes are drawn per sheet and per sentence, so
a code says nothing across rows. The key (code -> row letters) goes to
results/translate/ratings/key.json, which the rater must not open.

    python benchmarks/translate/rating_sheet.py build --sheet stage1-ru --target ru \
        --sermon results/translate/sermon150.jsonl \
        --mt recognized=results/translate/mt/<run> --systems B C D E --runs r1
    # stage 2: both versions, ratings already given in stage 1 reused
    python benchmarks/translate/rating_sheet.py build --sheet stage2-ru --target ru \
        --sermon ... --mt recognized=<run> clean=<run> --systems B C E \
        --reuse results/translate/ratings/stage1-ru.csv

score: reads the filled sheets, checks every value, then joins the key and
reports per system and version: rated rows, mean adequacy, share of critical
errors (overall and by type), mean fluency if filled.

    python benchmarks/translate/rating_sheet.py score results/translate/ratings/stage1-ru.csv [...]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import secrets
import string
import sys
from collections import defaultdict
from collections.abc import Set as AbstractSet
from pathlib import Path

RATINGS = Path(
    os.environ.get("BL_RATINGS_DIR")
    or Path(__file__).resolve().parents[2] / "results" / "translate" / "ratings"
)  # env override: dry runs keep the real key.json clean
COLUMNS = [
    "item_id", "window_id", "sentence_no", "version", "target_lang", "system",
    "reference_lt", "input_lt", "translation", "adequacy", "critical", "critical_type",
    "fluency", "note",
]  # fmt: skip
RATED = ("adequacy", "critical", "critical_type", "fluency", "note")
CRITICAL_TYPES = {"meaning", "name_number", "invented", "untranslated", "offensive"}
# Never a row letter (A-F, R), so a code cannot be mistaken for a system name.
CODE_LETTERS = [c for c in string.ascii_uppercase if c not in "ABCDEFR"]


def build_rows(
    sermon: list[dict],
    outputs: dict[str, dict[str, dict[str, str]]],  # version -> row letter -> id -> translation
    target: str,
    rng: random.Random,
    already_rated: AbstractSet[
        tuple[str, str, str]
    ] = frozenset(),  # (item_id, version, translation)
) -> tuple[list[dict], dict[str, dict[str, list[str]]]]:
    """Sheet rows and the key {item_id/version: {code: [row letters]}}."""
    rows: list[dict] = []
    key: dict[str, dict[str, list[str]]] = {}
    for rec in sermon:
        for version, by_row in outputs.items():
            groups: dict[str, list[str]] = {}
            for letter in sorted(by_row):
                text = by_row[letter].get(rec["id"])
                if text is None:
                    raise KeyError(f"row {letter} has no {version} translation for {rec['id']}")
                groups.setdefault(text, []).append(letter)
            texts = [t for t in groups if (rec["id"], version, t) not in already_rated]
            rng.shuffle(texts)
            codes = rng.sample(CODE_LETTERS, len(texts))
            key[f"{rec['id']}/{version}"] = {
                c: groups[t] for c, t in zip(codes, texts, strict=True)
            }
            for code, text in zip(codes, texts, strict=True):
                rows.append(
                    {
                        "item_id": rec["id"],
                        "window_id": rec["window_id"],
                        "sentence_no": rec["sentence_no"],
                        "version": version,
                        "target_lang": target,
                        "system": code,
                        "reference_lt": rec["clean"],
                        "input_lt": rec[version],
                        "translation": text,
                        **dict.fromkeys(RATED, ""),
                    }
                )
    return rows, key


def deblind(rows: list[dict], key: dict[str, dict[str, list[str]]]) -> list[tuple[str, dict]]:
    """(row letter, sheet row) for every system a rated row stands for."""
    out = []
    for r in rows:
        for letter in key[f"{r['item_id']}/{r['version']}"][r["system"]]:
            out.append((letter, r))
    return out


def validate(rows: list[dict]) -> list[str]:
    problems = []
    for n, r in enumerate(rows, 2):  # line 1 is the header
        where = f"line {n} ({r['item_id']} {r['system']})"
        if r["adequacy"].strip() not in {"0", "1", "2", "3"}:
            problems.append(f"{where}: adequacy must be 0-3")
        crit = r["critical"].strip() or "0"
        if crit not in {"0", "1"}:
            problems.append(f"{where}: critical must be 0 or 1")
        ctype = r["critical_type"].strip()
        if crit == "1" and ctype not in CRITICAL_TYPES:
            problems.append(f"{where}: critical_type must be one of {sorted(CRITICAL_TYPES)}")
        if crit == "0" and ctype:
            problems.append(f"{where}: critical_type given but critical is 0")
        if r["fluency"].strip() not in {"", "0", "1", "2"}:
            problems.append(f"{where}: fluency must be 0-2 or empty")
    return problems


def score(pairs: list[tuple[str, dict]]) -> list[dict]:
    acc: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"n": 0, "adequacy": 0, "critical": 0, "types": defaultdict(int), "flu": []}
    )
    for letter, r in pairs:
        a = acc[(letter, r["version"])]
        a["n"] += 1
        a["adequacy"] += int(r["adequacy"])
        if (r["critical"].strip() or "0") == "1":
            a["critical"] += 1
            a["types"][r["critical_type"].strip()] += 1
        if r["fluency"].strip():
            a["flu"].append(int(r["fluency"]))
    out = []
    for (letter, version), a in sorted(acc.items()):
        out.append(
            {
                "system": letter,
                "version": version,
                "rated": a["n"],
                "mean_adequacy": round(a["adequacy"] / a["n"], 3),
                "critical_share": round(a["critical"] / a["n"], 3),
                "critical_by_type": dict(a["types"]),
                "mean_fluency": round(sum(a["flu"]) / len(a["flu"]), 3) if a["flu"] else None,
            }
        )
    return out


# --------------------------------------------------------------------------- CLI


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def read_sheet(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_key() -> dict:
    path = RATINGS / "key.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def cmd_build(args: argparse.Namespace) -> int:
    RATINGS.mkdir(parents=True, exist_ok=True)
    sheet_path = RATINGS / f"{args.sheet}.csv"
    keys = load_key()
    if sheet_path.exists() or args.sheet in keys:
        sys.exit(f"{sheet_path} already built; pick a new --sheet name")
    sermon = read_jsonl(Path(args.sermon))
    if args.runs:
        sermon = [r for r in sermon if r["run"].rsplit("-", 1)[1] in args.runs]
    outputs: dict[str, dict[str, dict[str, str]]] = {}
    for spec in args.mt:
        version, run_dirs = spec.split("=", 1)  # several run dirs: comma-separated
        outputs[version] = {}
        for letter in args.systems:
            found = [
                Path(d) / f"{letter}.{args.target}.jsonl"
                for d in run_dirs.split(",")
                if (Path(d) / f"{letter}.{args.target}.jsonl").exists()
            ]
            if len(found) != 1:
                sys.exit(f"row {letter} ({version}): found in {len(found)} of {run_dirs}, need 1")
            outputs[version][letter] = {r["id"]: r["translation"] for r in read_jsonl(found[0])}
    already: set[tuple[str, str, str]] = set()
    for prev in args.reuse or []:
        already |= {(r["item_id"], r["version"], r["translation"]) for r in read_sheet(Path(prev))}
    seed = secrets.randbits(64)
    rows, key = build_rows(sermon, outputs, args.target, random.Random(seed), already)
    with sheet_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    keys[args.sheet] = {
        "seed": seed,
        "systems": args.systems,
        "mt": dict(s.split("=", 1) for s in args.mt),
        "reuse": args.reuse or [],
        "codes": key,
    }
    (RATINGS / "key.json").write_text(
        json.dumps(keys, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"{len(rows)} rows ({len(sermon)} sentences) -> {sheet_path}")
    return 0


def key_name(stem: str, keys: dict) -> str:
    """The sheet a filled file came from: its own name, or the one built-sheet
    name it contains (e.g. sol-stage1-ru-reconciled -> stage1-ru)."""
    if stem in keys:
        return str(stem)
    hits = [k for k in keys if k in stem]
    if len(hits) != 1:
        sys.exit(f"{stem}: matches {len(hits)} built sheets {hits}; rename it after one")
    return str(hits[0])


def cmd_score(args: argparse.Namespace) -> int:
    keys = load_key()
    pairs: list[tuple[str, dict]] = []
    bad = False
    for p in map(Path, args.sheets):
        rows = read_sheet(p)
        for msg in validate(rows):
            bad = True
            print(f"{p.name}: {msg}")
        if not bad:
            pairs += deblind(rows, keys[key_name(p.stem, keys)]["codes"])
    if bad:
        print("fix the sheets before scoring; the key was not read")
        return 1
    for line in score(pairs):
        print(json.dumps(line, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sheet", required=True, help="name, e.g. stage1-ru")
    b.add_argument("--target", choices=["ru", "en"], required=True)
    b.add_argument("--sermon", required=True)
    b.add_argument(
        "--mt",
        nargs="+",
        required=True,
        help="version=run_dir, e.g. recognized=results/translate/mt/x",
    )
    b.add_argument("--systems", nargs="+", required=True)
    b.add_argument("--runs", nargs="*", help="r1 / r2 (default: both)")
    b.add_argument("--reuse", nargs="*", help="filled sheets whose rows are not asked again")
    s = sub.add_parser("score")
    s.add_argument("sheets", nargs="+")
    args = ap.parse_args()
    return cmd_build(args) if args.cmd == "build" else cmd_score(args)


if __name__ == "__main__":
    raise SystemExit(main())
