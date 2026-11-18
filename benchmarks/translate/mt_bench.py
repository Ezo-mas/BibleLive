"""Phase 0, P0.3: run the translation rows over a set of Lithuanian sentences.

Runs in the scratch venv (~/.venvs/translate-p0), not the project venv.

    # once: download the models and convert the seq2seq ones to CTranslate2
    python benchmarks/translate/mt_bench.py prepare --rows B C D E

    # translate: input is JSONL with {"id": ..., "text": ...} per line, in order
    python benchmarks/translate/mt_bench.py run --rows B C D E --target ru \
        --input results/translate/sermon150.recognized.jsonl --run-name <unique>

Output: results/translate/mt/<run-name>/<row>.<target>.jsonl with
{"id", "source", "translation", "time_s"} per sentence, plus run.json with the
settings. Times are wall-clock per sentence with the model warm (first sentence
translated twice; the first pass is discarded).

Rows (docs/translate/PHASE0.md, P0.3):
  A  OPUS-MT tc-big-lt-en                LT->EN    CC-BY-4.0
  B  OPUS-MT tc-base-bat-zle (>>rus<<)   LT->RU    CC-BY-4.0
  C  MADLAD-400 3B, CTranslate2 int8     LT->EN/RU Apache-2.0
  D  EuroLLM-9B-Instruct Q4_K_M (GGUF)   LT->EN/RU Apache-2.0, 2 sentences of context
  E  pivot: A, then OPUS-MT tc-big-en-zle (>>rus<<)  LT->EN->RU  CC-BY-4.0
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = Path(
    os.environ.get("BL_TRANSLATE_MODELS", Path.home() / ".venvs" / "models" / "translate")
)
OUT_ROOT = REPO_ROOT / "results" / "translate" / "mt"

# name -> (Hugging Face repo, kind)
MODELS: dict[str, tuple[str, str]] = {
    "opus-lt-en": ("Helsinki-NLP/opus-mt-tc-big-lt-en", "marian"),
    "opus-bat-zle": ("Helsinki-NLP/opus-mt-tc-base-bat-zle", "marian"),
    "opus-en-zle": ("Helsinki-NLP/opus-mt-tc-big-en-zle", "marian"),
    "madlad-3b": ("google/madlad400-3b-mt", "t5"),
    "eurollm-9b": ("bartowski/EuroLLM-9B-Instruct-GGUF", "gguf:EuroLLM-9B-Instruct-Q4_K_M.gguf"),
}
ROW_MODELS = {
    "A": ["opus-lt-en"],
    "B": ["opus-bat-zle"],
    "C": ["madlad-3b"],
    "D": ["eurollm-9b"],
    "E": ["opus-lt-en", "opus-en-zle"],
}
ROW_TARGETS = {"A": {"en"}, "B": {"ru"}, "C": {"en", "ru"}, "D": {"en", "ru"}, "E": {"ru"}}
LANG_NAMES = {"en": "English", "ru": "Russian"}
OPUS_TARGET_TOKEN = {"ru": ">>rus<<"}


# --------------------------------------------------------------------------- prepare


def model_path(name: str) -> Path:
    repo, kind = MODELS[name]
    if kind.startswith("gguf:"):
        return MODELS_DIR / name / kind.split(":", 1)[1]
    return MODELS_DIR / f"{name}-ct2-int8"


def prepare(name: str) -> None:
    from huggingface_hub import hf_hub_download, snapshot_download

    repo, kind = MODELS[name]
    dst = model_path(name)
    if dst.exists():
        print(f"{name}: ready at {dst}")
        return
    if kind.startswith("gguf:"):
        hf_hub_download(repo, kind.split(":", 1)[1], local_dir=dst.parent)
    else:
        src = snapshot_download(
            repo, allow_patterns=["*.json", "*.model", "*.safetensors", "*.spm", "*.txt"]
        )
        subprocess.run(
            [
                sys.executable, "-m", "ctranslate2.converters.transformers",
                "--model", src, "--output_dir", str(dst), "--quantization", "int8",
                "--copy_files", *[f for f in ("source.spm", "target.spm", "spiece.model",
                                              "tokenizer_config.json", "special_tokens_map.json",
                                              "vocab.json") if (Path(src) / f).exists()],
            ],
            check=True,
        )  # fmt: skip
    print(f"{name}: ready at {dst}")


# --------------------------------------------------------------------------- translators


class CT2Translator:
    """A Marian or T5 model converted to CTranslate2, tokenized with its HF tokenizer."""

    def __init__(self, name: str, device: str, threads: int):
        if device == "cuda" and sys.platform == "win32":
            # nvidia-cublas-cu12 puts its DLLs here, off the default search path
            # (same fix as backend/audio/stt.py).
            for base in sys.path:
                for bin_dir in Path(base).glob("nvidia/*/bin"):
                    os.add_dll_directory(str(bin_dir))
                    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        import ctranslate2
        from transformers import AutoTokenizer

        self.name = name
        repo, self.kind = MODELS[name]
        self.tok = AutoTokenizer.from_pretrained(repo)
        # T5 (MADLAD) overflows in float16: its output degenerates into repeated
        # tokens. int8_bfloat16 fails the same way; int8_float32 is clean.
        gpu_compute = "int8_float32" if self.kind == "t5" else "int8_float16"
        compute = gpu_compute if device == "cuda" else "int8"
        self.model = ctranslate2.Translator(
            str(model_path(name)), device=device, compute_type=compute, intra_threads=threads
        )

    def translate(self, text: str, target: str, context: list[tuple[str, str]]) -> str:  # noqa: ARG002
        if self.kind == "t5":
            src = f"<2{target}> {text}"
        else:
            token = OPUS_TARGET_TOKEN.get(target)
            src = f"{token} {text}" if token and "zle" in self.name else text
        tokens = self.tok.convert_ids_to_tokens(self.tok.encode(src))
        result = self.model.translate_batch([tokens], beam_size=4, max_decoding_length=256)
        out = self.tok.convert_tokens_to_ids(result[0].hypotheses[0])
        return str(self.tok.decode(out, skip_special_tokens=True)).strip()


class LlamaTranslator:
    """EuroLLM through llama-cpp-python.

    Context: the two previous sentences of the same run, each with this row's own
    translation, as earlier chat turns, so the model sees them but answers
    only for the new sentence (a plain "context" paragraph made it translate
    the context too)."""

    SYSTEM = (
        "You are a professional interpreter translating a Christian sermon from Lithuanian. "
        "Translate only the sentence you are given, completely and faithfully, keeping the "
        "speaker's meaning. Do not add explanations."
    )
    RU_NOTES = (
        " When the preacher addresses the congregation, use the plural вы. Write Bible books "
        "and names in their usual Russian Synodal form."
    )

    # llama-cpp-python's create_chat_completion default; sent to llama-server too
    # (whose own default is 1.0) so both backends decode the same way.
    REPEAT_PENALTY = 1.1

    def __init__(self, name: str, device: str, threads: int):
        # BL_LLAMA_SERVER: base URL of a running llama-server (e.g. a CUDA build
        # where the llama-cpp-python wheel has none); the model is whatever it loaded.
        self.server = os.environ.get("BL_LLAMA_SERVER", "").rstrip("/")
        if self.server:
            return
        from llama_cpp import Llama

        self.model = Llama(
            model_path=str(os.environ.get("BL_GGUF") or model_path(name)),
            n_ctx=2048,
            n_gpu_layers=-1 if device == "cuda" else 0,
            n_threads=threads or None,
            verbose=False,
        )

    def _chat(self, messages: list[dict]) -> str:
        if not self.server:
            out = self.model.create_chat_completion(
                messages=messages,
                temperature=0.0,
                max_tokens=256,
                repeat_penalty=self.REPEAT_PENALTY,
            )
            return str(out["choices"][0]["message"]["content"])
        import urllib.request

        body = {
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 256,
            "repeat_penalty": self.REPEAT_PENALTY,
        }
        req = urllib.request.Request(
            f"{self.server}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return str(json.loads(resp.read())["choices"][0]["message"]["content"])

    def translate(self, text: str, target: str, context: list[tuple[str, str]]) -> str:
        lang = LANG_NAMES[target]
        system = self.SYSTEM + (self.RU_NOTES if target == "ru" else "")
        ask = f"Translate this Lithuanian sentence into {lang}. Output only the translation.\n\n"
        messages = [{"role": "system", "content": system}]
        for src, tgt in context[-2:]:
            messages += [
                {"role": "user", "content": ask + src},
                {"role": "assistant", "content": tgt},
            ]
        messages.append({"role": "user", "content": ask + text})
        return self._chat(messages).strip()


def load(name: str, device: str, threads: int):
    kind = MODELS[name][1]
    cls = LlamaTranslator if kind.startswith("gguf:") else CT2Translator
    return cls(name, device, threads)


# --------------------------------------------------------------------------- run


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def run_row(
    row: str, target: str, items: list[dict], device: str, threads: int, out_dir: Path
) -> dict:
    models = [load(n, device, threads) for n in ROW_MODELS[row]]
    records: list[dict] = []
    times, hop1, hop2 = [], [], []

    def translate(i: int, text: str) -> tuple[str, float, list]:
        # Previous sentences of the same run (``run`` key; all one run if absent),
        # with this row's own translations of them.
        run_id = items[i].get("run")
        context = [
            (r["source"], r["translation"])
            for it, r in zip(items[max(0, i - 2) : i], records[max(0, i - 2) : i], strict=True)
            if it.get("run") == run_id
        ]
        if row == "E":
            t0 = time.perf_counter()
            en = models[0].translate(text, "en", context)
            t1 = time.perf_counter()
            ru = models[1].translate(en, target, [])
            t2 = time.perf_counter()
            return ru, t2 - t0, [t1 - t0, t2 - t1, en]
        t0 = time.perf_counter()
        out = models[0].translate(text, target, context)
        return out, time.perf_counter() - t0, []

    if items:
        translate(0, items[0]["text"])  # warm-up, discarded
    for i, it in enumerate(items):
        out, dt, hops = translate(i, it["text"])
        rec = {"id": it["id"], "source": it["text"], "translation": out, "time_s": round(dt, 4)}
        if hops:
            rec.update(hop1_s=round(hops[0], 4), hop2_s=round(hops[1], 4), pivot_en=hops[2])
            hop1.append(hops[0])
            hop2.append(hops[1])
        records.append(rec)
        times.append(dt)

    path = out_dir / f"{row}.{target}.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    summary = {"row": row, "target": target, "n": len(records), "file": path.name}
    if times:
        summary.update(p50_s=round(statistics.median(times), 3), p95_s=round(pct(times, 0.95), 3))
    if hop1:
        summary.update(
            hop1_p50_s=round(statistics.median(hop1), 3), hop1_p95_s=round(pct(hop1, 0.95), 3),
            hop2_p50_s=round(statistics.median(hop2), 3), hop2_p95_s=round(pct(hop2, 0.95), 3),
        )  # fmt: skip
    models.clear()  # free the model before the next row loads
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--rows", nargs="+", default=list(ROW_MODELS))
    r = sub.add_parser("run")
    r.add_argument("--rows", nargs="+", required=True)
    r.add_argument("--target", choices=["en", "ru"], required=True)
    r.add_argument("--input", required=True)
    r.add_argument("--run-name", required=True)
    r.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    r.add_argument("--threads", type=int, default=0)
    r.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.cmd == "prepare":
        for name in dict.fromkeys(n for row in args.rows for n in ROW_MODELS[row]):
            prepare(name)
        return 0

    out_dir = OUT_ROOT / args.run_name
    if out_dir.exists():
        sys.exit(f"{out_dir} exists: pick a new --run-name")
    items = [
        json.loads(line)
        for line in Path(args.input).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        items = items[: args.limit]
    out_dir.mkdir(parents=True)
    summaries = []
    for row in args.rows:
        if args.target not in ROW_TARGETS[row]:
            print(f"row {row}: no {args.target} output, skipped")
            continue
        s = run_row(row, args.target, items, args.device, args.threads, out_dir)
        print(json.dumps(s))
        summaries.append(s)
    meta = {
        "input": str(args.input), "target": args.target, "device": args.device,
        "threads": args.threads, "host": platform.node(), "rows": summaries,
        "models": {n: MODELS[n][0] for row in args.rows for n in ROW_MODELS[row]},
        "llama_server": os.environ.get("BL_LLAMA_SERVER") or None,
        "gguf": os.environ.get("BL_GGUF") or None,
    }  # fmt: skip
    (out_dir / "run.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
