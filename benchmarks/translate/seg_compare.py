"""Phase 0: CPU-tier segment choice. Translate the default and t06 transcripts, compare.

The CPU tier (Kmynas on CPU + OPUS-MT row B) cannot afford S2, so it must pick
between BibleLive's default segments (1.5 s pause; long, whole sentences, text
delay p95 10 s) and t06 fragments (0.6 s pause; p95 6.4 s, but Kmynas puts a full
stop at most cuts). This measures what the fragments cost in translation.

Units: the default segments of a window are merged at every default cut that has
no t06 cut inside its pause, so each unit holds whole segments of both settings.
Corrected reference words go to a unit by their interpolated time.

    # project venv or translate-p0 venv: write the three inputs
    python benchmarks/translate/seg_compare.py inputs --stt results/translate/stt/p02/kmynas-cpu
    # translate-p0 venv: row B on CPU, three threads, once per input
    python benchmarks/translate/mt_bench.py run --rows B --target ru --device cpu --threads 3 \
        --input results/translate/segcmp/default.jsonl --run-name segcmp-default
    (same for t06.jsonl, ref.jsonl, refcut.jsonl and joined.jsonl)
    # translate-p0 venv (sacrebleu): scores, plus a blind A/B sheet
    python benchmarks/translate/seg_compare.py report --default <run> --t06 <run> --ref <run>         --refcut <run> [--joined <run>]

Score: chrF++ of each setting's unit translation against row B's translation of
the corrected unit text, so the same model is on both sides and the difference is
what recognition plus segmentation cost. Paired bootstrap over units for the gap.
Control: the corrected text cut at each setting's segment boundaries (refcut), so
the gap there is segmentation alone, without the extra recognition errors.

Joined (commit policies over the t06 pieces, inside a unit, i.e. a group is also
flushed at a default cut): ``jp`` joins pieces until one ends a sentence (. ? ! …),
capped at 12 s of span; ``jm4`` the same but a sentence end closes the group only
once it spans 4 s; ``jm4d`` is jm4 with the joint de-punctuated (a trailing full
stop dropped and the next piece's first letter lowered, as Kmynas writes them at
most cuts). Delay: per hypothesis word, from its interpolated end to the moment its
group's translation exists (one recognition worker in order, measured times, then
the measured MT time of the group).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import secrets
import statistics
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_sermon150 import WINDOW_IDS, parse  # noqa: E402

ROOT = Path(os.environ.get("BL_TRANSLATE_RESULTS") or HERE.parents[1] / "results" / "translate")
OUT = ROOT / "segcmp"
EPS_S = 0.15  # a t06 cut counts as inside a default pause within this slack
SENT_END = re.compile(r"[.?!…][\"“”„»)\]’']*$")
POLICIES = {"jp": (0.0, False), "jm4": (4.0, False), "jm4d": (4.0, True)}  # min span, depunct
MAX_SPAN_S = 12.0
SYSTEMS = ("default", "t06", *POLICIES)


def build_units(
    default: list[dict], t06: list[dict], lines: list[tuple[float, float, str]]
) -> list[dict]:
    """Units for one window: lists of default and t06 segments plus reference words."""
    d = sorted((s for s in default if s["text"].strip()), key=lambda s: s["t0"])
    t = sorted((s for s in t06 if s["text"].strip()), key=lambda s: s["t0"])
    t_cuts = [(a["t1"], b["t0"]) for a, b in zip(t, t[1:], strict=False)]
    spans: list[list[dict]] = []
    for i, seg in enumerate(d):
        if i and not any(a >= d[i - 1]["t1"] - EPS_S and b <= seg["t0"] + EPS_S for a, b in t_cuts):
            spans[-1].append(seg)
        else:
            spans.append([seg])
    bounds = [(sp[0]["t0"], sp[-1]["t1"]) for sp in spans]

    def unit_of(time_s: float) -> int:
        for k, (_a, b) in enumerate(bounds):
            nxt = bounds[k + 1][0] if k + 1 < len(bounds) else float("inf")
            if time_s < (b + nxt) / 2:
                return k
        return len(bounds) - 1

    units: list[dict[str, Any]] = [
        {"default": sp, "t06": [], "ref": [], "ref_t": [], "skip": False} for sp in spans
    ]
    for s in t:
        units[unit_of((s["t0"] + s["t1"]) / 2)]["t06"].append(s)
    for a, b, text in lines:
        if text == "<skip>":
            for k, (u0, u1) in enumerate(bounds):
                if a <= (u0 + u1) / 2 <= b:
                    units[k]["skip"] = True
            continue
        toks = text.split()
        for j, tok in enumerate(toks):
            mid = a + (b - a) * (j + 0.5) / len(toks)
            units[unit_of(mid)]["ref"].append(tok)
            units[unit_of(mid)]["ref_t"].append((tok, mid))
    return units


def cut_words(words: list[tuple[str, float]], segs: list[dict]) -> list[str]:
    """Reference words split at a setting's cuts: each word to the nearest segment."""
    pieces: list[list[str]] = [[] for _ in segs]
    for tok, t in words:
        k = min(range(len(segs)), key=lambda i: max(segs[i]["t0"] - t, t - segs[i]["t1"], 0))
        pieces[k].append(tok)
    return [" ".join(p) for p in pieces if p]


def join_groups(segs: list[dict], min_span: float) -> list[list[int]]:
    """Indices of segs grouped by a commit policy (see the module doc)."""
    groups: list[list[int]] = []
    cur: list[int] = []
    for i, s in enumerate(segs):
        if cur and s["t1"] - segs[cur[0]]["t0"] > MAX_SPAN_S:
            groups.append(cur)
            cur = []
        cur.append(i)
        span = s["t1"] - segs[cur[0]]["t0"]
        if SENT_END.search(s["text"].strip()) and span >= min_span:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def join_text(texts: list[str], depunct: bool) -> str:
    out = texts[0].strip()
    for t in texts[1:]:
        t = t.strip()
        if depunct and out.endswith(".") and not out.endswith("..") and t[:1].isupper():
            out, t = out[:-1], t[:1].lower() + t[1:]
        out = f"{out} {t}"
    return out


def pct(v: list[float], p: float) -> float:
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


def cmd_inputs(args: argparse.Namespace) -> int:
    stt = Path(args.stt)
    segs = {name: read_jsonl(stt / f"{name}.jsonl") for name in ("default", "t06")}
    OUT.mkdir(parents=True, exist_ok=True)
    units_out: list[dict] = []
    inputs: dict[str, list[dict]] = {
        "default": [], "t06": [], "ref": [], "refcut": [], "joined": []
    }  # fmt: skip
    for w in WINDOW_IDS:
        _, lines = parse(ROOT / "drafts" / f"{w}.txt")
        per = {k: [s for s in v if s["window"] == w] for k, v in segs.items()}
        for n, u in enumerate(build_units(per["default"], per["t06"], lines), 1):
            uid = f"{w}-{n:03d}"
            if u["skip"] or not u["ref"]:
                continue
            rec: dict[str, Any] = {"id": uid, "window": w, "ref_lt": " ".join(u["ref"])}
            for k in ("default", "t06"):
                ids = []
                for j, s in enumerate(u[k], 1):
                    sid = f"{uid}-{k}-{j}"
                    inputs[k].append({"id": sid, "text": s["text"].strip()})
                    ids.append(sid)
                rec[k] = ids
                rec[f"{k}_lt"] = [s["text"].strip() for s in u[k]]
                rec[f"{k}_segs"] = [
                    {f: s[f] for f in ("t0", "t1", "close", "ms", "text")} for s in u[k]
                ]
                cut = []
                for j, text in enumerate(cut_words(u["ref_t"], u[k]), 1):
                    sid = f"{uid}-refcut-{k}-{j}"
                    inputs["refcut"].append({"id": sid, "text": text})
                    cut.append(sid)
                rec[f"refcut_{k}"] = cut
            inputs["ref"].append({"id": uid, "text": rec["ref_lt"]})
            for name, (min_span, depunct) in POLICIES.items():
                groups = join_groups(u["t06"], min_span)
                ids = []
                for j, g in enumerate(groups, 1):
                    sid = f"{uid}-{name}-{j}"
                    text = join_text([u["t06"][i]["text"] for i in g], depunct)
                    inputs["joined"].append({"id": sid, "text": text})
                    ids.append(sid)
                rec[name] = ids
                rec[f"{name}_groups"] = groups
            units_out.append(rec)
    for k, rows in inputs.items():
        write_jsonl(OUT / f"{k}.jsonl", rows)
    (OUT / "units.json").write_text(json.dumps(units_out, ensure_ascii=False, indent=1), "utf-8")
    print(
        f"{len(units_out)} units; segments default {len(inputs['default'])}, "
        f"t06 {len(inputs['t06'])} -> {OUT}"
    )
    return 0


def load_mt(run: str) -> dict[str, str]:
    path = ROOT / "mt" / run / "B.ru.jsonl"
    return {r["id"]: r["translation"].strip() for r in read_jsonl(path)}


def bootstrap(diffs: list[tuple[str, str, str]], n: int = 1000) -> tuple[float, float]:
    """95% interval of corpus chrF++(b) - chrF++(a) over resampled units."""
    from sacrebleu.metrics import CHRF

    chrf = CHRF(word_order=2)
    rng = random.Random(0)
    gaps = []
    for _ in range(n):
        idx = [rng.randrange(len(diffs)) for _ in diffs]
        refs = [[diffs[i][0] for i in idx]]
        a = chrf.corpus_score([diffs[i][1] for i in idx], refs).score
        b = chrf.corpus_score([diffs[i][2] for i in idx], refs).score
        gaps.append(b - a)
    gaps.sort()
    return gaps[int(0.025 * n)], gaps[int(0.975 * n) - 1]


def load_times(run: str) -> dict[str, float]:
    path = ROOT / "mt" / run / "B.ru.jsonl"
    return {r["id"]: float(r["time_s"]) for r in read_jsonl(path)}


def delays(units: list[dict], system: str, mt_time: dict[str, float]) -> list[float]:
    """Per hypothesis word: seconds from its end to its group's translation."""
    base = "default" if system == "default" else "t06"
    out: list[float] = []
    free: dict[str, float] = {}
    for u in units:
        segs = u[f"{base}_segs"]
        avail = []
        for s in segs:  # one recognition worker per window, in order
            f = max(free.get(u["window"], 0.0), s["close"]) + s["ms"] / 1000
            free[u["window"]] = f
            avail.append(f)
        groups = u.get(f"{system}_groups") or [[i] for i in range(len(segs))]
        for gid, g in zip(u[system], groups, strict=True):
            ready = avail[g[-1]] + mt_time[gid]
            for i in g:
                s = segs[i]
                toks = s["text"].split()
                for k in range(len(toks)):
                    end = s["t0"] + (s["t1"] - s["t0"]) * (k + 1) / len(toks)
                    out.append(ready - end)
    return out


def cmd_report(args: argparse.Namespace) -> int:
    from sacrebleu.metrics import CHRF

    chrf = CHRF(word_order=2)
    units = json.loads((OUT / "units.json").read_text("utf-8"))
    runs = {k: getattr(args, k) for k in ("default", "t06", "ref", "refcut", "joined")}
    runs = {k: v for k, v in runs.items() if v}
    mt = {k: load_mt(v) for k, v in runs.items()}
    times = {k: load_times(v) for k, v in runs.items()}
    systems = [s for s in SYSTEMS if s in ("default", "t06") or "joined" in mt]
    src = {s: ("joined" if s in POLICIES else s) for s in systems}
    rows = []
    for u in units:
        row = {**u, "ref_ru": mt["ref"][u["id"]]}
        for s in systems:
            row[f"{s}_ru"] = " ".join(mt[src[s]][i] for i in u[s])
        row["refcut_default_ru"] = " ".join(mt["refcut"][i] for i in u["refcut_default"])
        row["refcut_t06_ru"] = " ".join(mt["refcut"][i] for i in u["refcut_t06"])
        rows.append(row)
    refs = [[r["ref_ru"] for r in rows]]

    def compare(a_key: str, b_key: str) -> dict:
        triples = [(r["ref_ru"], r[a_key], r[b_key]) for r in rows]
        a = chrf.corpus_score([x[1] for x in triples], refs).score
        b = chrf.corpus_score([x[2] for x in triples], refs).score
        lo, hi = bootstrap(triples)
        return {
            "chrf": round(b, 2),
            "gap_vs_default": round(b - a, 2),
            "gap_95ci": [round(lo, 2), round(hi, 2)],
        }

    table = {}
    for s in systems:
        d = delays(units, s, times[src[s]])
        entry = compare("default_ru", f"{s}_ru")
        entry.update(
            {
                "translated_units": sum(len(u[s]) for u in units),
                "delay_p50_s": round(statistics.median(d), 2),
                "delay_p95_s": round(pct(d, 95), 2),
            }
        )
        table[s] = entry
    control = compare("refcut_default_ru", "refcut_t06_ru")
    control["chrf_default"] = round(
        chrf.corpus_score([r["refcut_default_ru"] for r in rows], refs).score, 2
    )
    for r in rows:
        for s in systems:
            r[f"chrf_{s}"] = round(chrf.sentence_score(r[f"{s}_ru"], [r["ref_ru"]]).score, 1)
    report = {
        "units": len(rows),
        "systems": table,
        "control_corrected_text_cut_default_vs_t06": control,
        "runs": runs,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=1), "utf-8")
    write_jsonl(OUT / "units-scored.jsonl", rows)
    print(json.dumps(report, indent=1))
    if (OUT / "segcmp-ru-ab.csv").exists():
        print("sheet exists: kept (its key would change)")
    else:
        write_sheet(rows)
    return 0


def write_sheet(rows: list[dict]) -> None:
    """Blind A/B sheet of the units whose translations differ; key kept apart."""
    rng = random.Random(secrets.randbits(64))
    key, out = {}, []
    for r in rows:
        if r["default_ru"] == r["t06_ru"]:
            continue
        pair = [("default", r["default_ru"]), ("t06", r["t06_ru"])]
        rng.shuffle(pair)
        key[r["id"]] = {"X": pair[0][0], "Y": pair[1][0]}
        out.append(
            {
                "item_id": r["id"],
                "reference_lt": r["ref_lt"],
                "translation_x": pair[0][1],
                "translation_y": pair[1][1],
                "better": "",
                "critical_x": "",
                "critical_y": "",
                "note": "",
            }
        )
    path = OUT / "segcmp-ru-ab.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    (OUT / "segcmp-ru-ab.key.json").write_text(json.dumps(key, indent=1), "utf-8")
    print(f"sheet: {path} ({len(out)} units differ); key apart, do not show the rater")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("inputs")
    i.add_argument("--stt", required=True, help="stt_bench output dir with default/t06.jsonl")
    r = sub.add_parser("report")
    for k in ("default", "t06", "ref", "refcut"):
        r.add_argument(f"--{k}", required=True, help="mt_bench run name")
    r.add_argument("--joined", help="mt_bench run name of joined.jsonl")
    args = ap.parse_args()
    return cmd_inputs(args) if args.cmd == "inputs" else cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
