"""Phase 0, P0.3: FLORES-200 devtest scores (BLEU, chrF++, COMET-22) for the MT rows.

FLORES+ (openlanguagedata/flores_plus, CC-BY-SA-4.0) is gated on Hugging Face:
the account must accept its terms once.

    # 1. fetch devtest Lithuanian, English, Russian (scratch venv ~/.venvs/translate-p0)
    python benchmarks/translate/flores_bench.py fetch
    # 2. translate with mt_bench.py (same venv), input results/translate/flores/lit.jsonl
    python benchmarks/translate/mt_bench.py run --rows A D --target en \
        --input results/translate/flores/lit.jsonl --run-name flores-en-gpu
    # 3. score (COMET venv ~/.venvs/comet-p0: unbabel-comet, sacrebleu)
    python benchmarks/translate/flores_bench.py score flores-en-gpu flores-ru-gpu

FLORES sentences come from short articles in order, so mt_bench's two sentences
of context are usually the same article's previous sentences (across an article
boundary they are unrelated text).

Output: results/translate/mt/<run>/scores.json and a table on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.environ.get("BL_RESULTS", REPO_ROOT / "results"))
FLORES_DIR = RESULTS / "translate" / "flores"
MT_DIR = RESULTS / "translate" / "mt"
DATASET = "openlanguagedata/flores_plus"
LANGS = {"lit": "lit_Latn", "eng": "eng_Latn", "rus": "rus_Cyrl"}
REF_FOR_TARGET = {"en": "eng", "ru": "rus"}
COMET_MODEL = "Unbabel/wmt22-comet-da"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def fetch() -> None:
    from huggingface_hub import hf_hub_download

    FLORES_DIR.mkdir(parents=True, exist_ok=True)
    for short, code in LANGS.items():
        src = hf_hub_download(DATASET, f"devtest/{code}.jsonl", repo_type="dataset")
        rows = read_jsonl(Path(src))
        out = [{"id": int(r["id"]), "text": str(r["text"]).strip()} for r in rows]
        out.sort(key=lambda r: int(str(r["id"])))
        (FLORES_DIR / f"{short}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8"
        )
        print(f"{short}: {len(out)} sentences")


def align(
    hyps: list[dict[str, Any]], srcs: dict[int, str], refs: dict[int, str]
) -> tuple[list[str], list[str], list[str]]:
    """Source, hypothesis and reference lists in hypothesis order, matched by id."""
    s, h, r = [], [], []
    for item in hyps:
        i = int(item["id"])
        if i not in refs or i not in srcs:
            raise KeyError(f"id {i} has no FLORES source or reference")
        s.append(srcs[i])
        h.append(str(item["translation"]).strip())
        r.append(refs[i])
    return s, h, r


def score_run(run: str, comet_model: Any, batch: int) -> list[dict[str, Any]]:
    import sacrebleu

    run_dir = MT_DIR / run
    srcs = {r["id"]: r["text"] for r in read_jsonl(FLORES_DIR / "lit.jsonl")}
    rows = []
    for f in sorted(run_dir.glob("*.*.jsonl")):
        row, target = f.name.split(".")[:2]
        if target not in REF_FOR_TARGET:
            continue
        refs = {
            r["id"]: r["text"] for r in read_jsonl(FLORES_DIR / f"{REF_FOR_TARGET[target]}.jsonl")
        }
        s, h, r = align(read_jsonl(f), srcs, refs)
        bleu = sacrebleu.corpus_bleu(h, [r])
        chrf = sacrebleu.corpus_chrf(h, [r], word_order=2)
        data = [{"src": a, "mt": b, "ref": c} for a, b, c in zip(s, h, r, strict=True)]
        comet = comet_model.predict(data, batch_size=batch, gpus=0, progress_bar=False)
        rows.append({
            "run": run, "row": row, "target": target, "n": len(h),
            "bleu": round(bleu.score, 2), "chrf++": round(chrf.score, 2),
            "comet22": round(float(comet.system_score), 4),
        })  # fmt: skip
    (run_dir / "scores.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    s = sub.add_parser("score")
    s.add_argument("runs", nargs="+")
    s.add_argument("--batch", type=int, default=16)
    args = ap.parse_args()
    if args.cmd == "fetch":
        fetch()
        return 0
    from comet import download_model, load_from_checkpoint

    model = load_from_checkpoint(download_model(COMET_MODEL))
    print(f"{'run':24} {'row':4} {'tgt':4} {'n':>5} {'BLEU':>6} {'chrF++':>7} {'COMET':>7}")
    for run in args.runs:
        if not (MT_DIR / run).is_dir():
            sys.exit(f"no run {run} under {MT_DIR}")
        for r in score_run(run, model, args.batch):
            print(f"{r['run']:24} {r['row']:4} {r['target']:4} {r['n']:5} "
                  f"{r['bleu']:6.2f} {r['chrf++']:7.2f} {r['comet22']:7.4f}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
