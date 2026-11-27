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
CPU-tier screen (2026-11-26):
  G  OPUS-MT tc-bible-big-bat-en         LT->EN    Apache-2.0
  H  HPLT v2.0 lt-en (hplt_opus, Marian) LT->EN    CC-BY-4.0
  I  OPUS-MT tc-bible-big-mul-mul (>>rus<<)  LT->RU  Apache-2.0
  J  OPUS-MT lt-ru (2020)                LT->RU    CC-BY-4.0
Cloud check (2026-11-27):
  K  any OpenAI-compatible chat API, row D's prompt and context  LT->EN/RU
     BL_OPENAI_BASE   base URL; requests go to <base>/chat/completions
                      (Gemini: https://generativelanguage.googleapis.com/v1beta/openai)
     BL_OPENAI_MODEL  model id (e.g. gemini-4-hydrogen)
     BL_OPENAI_KEY_ENV  name of the env var holding the key (default OPENAI_API_KEY);
                      the key itself is never written to run.json
     BL_OPENAI_EFFORT reasoning_effort to send (unset = provider default)
     BL_OPENAI_RPM    max requests per minute (free tiers); waits are not timed
     BL_OPENAI_EXTRA  JSON merged into the request body (provider-specific options)
     BL_OPENAI_NO_SYSTEM=1  fold the system prompt into the first user turn (models
                      without a system role, e.g. Gemma on the Gemini API)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
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
    "opus-bible-bat-en": ("Helsinki-NLP/opus-mt-tc-bible-big-bat-en", "marian"),
    "hplt-lt-en": ("HPLT/translate-lt-en-v2.0-hplt_opus", "marian-npz"),
    "opus-bible-mul": ("Helsinki-NLP/opus-mt-tc-bible-big-mul-mul", "marian"),
    "opus-lt-ru": ("Helsinki-NLP/opus-mt-lt-ru", "marian"),
    "openai-api": (os.environ.get("BL_OPENAI_MODEL", "(BL_OPENAI_MODEL unset)"), "api"),
}
# Multi-target OPUS models that need a target-language token before the source.
TARGET_TOKEN_MODELS = {"opus-bat-zle", "opus-en-zle", "opus-bible-mul"}
ROW_MODELS = {
    "A": ["opus-lt-en"],
    "B": ["opus-bat-zle"],
    "C": ["madlad-3b"],
    "D": ["eurollm-9b"],
    "E": ["opus-lt-en", "opus-en-zle"],
    "G": ["opus-bible-bat-en"],
    "H": ["hplt-lt-en"],
    "I": ["opus-bible-mul"],
    "J": ["opus-lt-ru"],
    "K": ["openai-api"],
}
ROW_TARGETS = {
    "A": {"en"}, "B": {"ru"}, "C": {"en", "ru"}, "D": {"en", "ru"}, "E": {"ru"},
    "G": {"en"}, "H": {"en"}, "I": {"ru"}, "J": {"ru"}, "K": {"en", "ru"},
}  # fmt: skip
LANG_NAMES = {"en": "English", "ru": "Russian"}
OPUS_TARGET_TOKEN = {"ru": ">>rus<<"}
# Row D's prompt for other GGUF models (BL_GGUF / llama-server): "chat" (EuroLLM, default),
# "translategemma" (its own template, llama-server only), "nothink" (chat with thinking off).
PROMPT_STYLE = os.environ.get("BL_PROMPT_STYLE", "chat")
# BL_PROMPT_FILE: JSON with prompt variants (benchmarks/translate/prompts/), keys per target
# language: "system" replaces row D's system prompt; "translategemma_notes" is inserted into
# TranslateGemma's template before its "Produce only ..." line. Unset = the v1 prompts below.
PROMPT_FILE = os.environ.get("BL_PROMPT_FILE", "")
PROMPTS: dict[str, dict[str, str]] = (
    json.loads(Path(PROMPT_FILE).read_text(encoding="utf-8")) if PROMPT_FILE else {}
)


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
    elif kind == "marian-npz":
        # Native Marian release (HPLT): .npz + SentencePiece model; CTranslate2's Marian
        # converter wants a YAML vocabulary, built here from the SentencePiece vocab order.
        npz_dir = Path(snapshot_download(repo))
        vocab = next(npz_dir.glob("*.vocab"))
        yml = dst.parent / f"{name}.vocab.yml"
        yml.parent.mkdir(parents=True, exist_ok=True)
        lines = vocab.read_text(encoding="utf-8").splitlines()
        pieces = [line.split("\t")[0] for line in lines]
        yml.write_text(
            "".join(f"{json.dumps(p, ensure_ascii=False)}: {i}\n" for i, p in enumerate(pieces)),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable, "-m", "ctranslate2.converters.marian",
                "--model_path", str(next(npz_dir.glob("*.npz"))), "--vocab_paths", str(yml), str(yml),
                "--output_dir", str(dst), "--quantization", "int8",
            ],
            check=True,
        )  # fmt: skip
        (dst / "source.spm").write_bytes(next(npz_dir.glob("*.spm")).read_bytes())
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
        if self.kind == "marian-npz":
            import sentencepiece

            self.spm = sentencepiece.SentencePieceProcessor(
                model_file=str(model_path(name) / "source.spm")
            )
        else:
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
        elif self.kind == "marian-npz":
            pieces = [*self.spm.encode(text, out_type=str), "</s>"]
            result = self.model.translate_batch([pieces], beam_size=4, max_decoding_length=256)
            return str(self.spm.decode(result[0].hypotheses[0])).strip()
        else:
            token = OPUS_TARGET_TOKEN.get(target)
            src = f"{token} {text}" if token and self.name in TARGET_TOKEN_MODELS else text
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

    def _post(self, path: str, body: dict) -> dict:
        import urllib.request

        req = urllib.request.Request(
            f"{self.server}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return dict(json.loads(resp.read()))

    def _translategemma(self, text: str, target: str, context: list[tuple[str, str]]) -> str:
        """TranslateGemma's own chat template (chat_template.jinja of google/translategemma-*),
        rendered here because llama-server's OpenAI endpoint drops the per-message language
        codes it needs. Same two sentences of context as earlier turns; BOS is added by the
        server."""
        tgt = LANG_NAMES[target]
        notes = PROMPTS.get("translategemma_notes", {}).get(target, "")
        notes = f"{notes.strip()}\n" if notes else ""

        def user(src: str) -> str:
            return (
                "<start_of_turn>user\nYou are a professional Lithuanian (lt) to "
                f"{tgt} ({target}) translator. Your goal is to accurately convey the meaning and "
                f"nuances of the original Lithuanian text while adhering to {tgt} grammar, "
                f"vocabulary, and cultural sensitivities.\n{notes}Produce only the {tgt} translation, "
                "without any additional explanations or commentary. Please translate the "
                f"following Lithuanian text into {tgt}:\n\n\n{src.strip()}<end_of_turn>\n"
            )

        prompt = "".join(
            user(src) + f"<start_of_turn>model\n{tgt_text.strip()}<end_of_turn>\n"
            for src, tgt_text in context[-2:]
        )
        prompt += user(text) + "<start_of_turn>model\n"
        out = self._post(
            "/completion",
            {
                "prompt": prompt,
                "temperature": 0.0,
                "n_predict": 256,
                "repeat_penalty": self.REPEAT_PENALTY,
                "stop": ["<end_of_turn>"],
            },
        )
        return str(out["content"])

    def _chat(self, messages: list[dict]) -> str:
        if not self.server:
            out = self.model.create_chat_completion(
                messages=messages,
                temperature=0.0,
                max_tokens=256,
                repeat_penalty=self.REPEAT_PENALTY,
            )
            return str(out["choices"][0]["message"]["content"])
        body: dict = {
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 256,
            "repeat_penalty": self.REPEAT_PENALTY,
        }
        if PROMPT_STYLE == "nothink":
            # Qwen3.x and other hybrid-reasoning models: answer directly.
            body["chat_template_kwargs"] = {"enable_thinking": False}
        out = self._post("/v1/chat/completions", body)
        return str(out["choices"][0]["message"]["content"])

    def translate(self, text: str, target: str, context: list[tuple[str, str]]) -> str:
        if PROMPT_STYLE == "translategemma":
            if not self.server:
                sys.exit("BL_PROMPT_STYLE=translategemma needs BL_LLAMA_SERVER")
            return self._translategemma(text, target, context).strip()
        lang = LANG_NAMES[target]
        system = PROMPTS.get("system", {}).get(target) or (
            self.SYSTEM + (self.RU_NOTES if target == "ru" else "")
        )
        ask = f"Translate this Lithuanian sentence into {lang}. Output only the translation.\n\n"
        messages = [{"role": "system", "content": system}]
        for src, tgt in context[-2:]:
            messages += [
                {"role": "user", "content": ask + src},
                {"role": "assistant", "content": tgt},
            ]
        messages.append({"role": "user", "content": ask + text})
        return self._chat(messages).strip()


class ApiTranslator(LlamaTranslator):
    """Row K: an OpenAI-compatible chat API (cloud or local) with row D's prompt and
    context. ``waited_s`` collects rate-limit pacing and retry back-off so run_row can
    leave it out of the per-sentence time; ``usage`` sums the reported tokens."""

    def __init__(self, name: str, device: str, threads: int):  # noqa: ARG002
        self.base = os.environ.get("BL_OPENAI_BASE", "").rstrip("/")
        self.model_id = os.environ.get("BL_OPENAI_MODEL", "")
        key_env = os.environ.get("BL_OPENAI_KEY_ENV", "OPENAI_API_KEY")
        self.key = os.environ.get(key_env, "")
        if not (self.base and self.model_id and self.key):
            sys.exit(f"row K needs BL_OPENAI_BASE, BL_OPENAI_MODEL and ${key_env}")
        self.effort = os.environ.get("BL_OPENAI_EFFORT", "")
        rpm = float(os.environ.get("BL_OPENAI_RPM", "0") or 0)
        self.gap_s = 60.0 / rpm if rpm > 0 else 0.0
        self.last_call = 0.0
        self.waited_s = 0.0
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.extra = json.loads(os.environ.get("BL_OPENAI_EXTRA") or "{}")

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)
        self.waited_s += seconds

    def _chat(self, messages: list[dict]) -> str:
        import urllib.error
        import urllib.request

        if os.environ.get("BL_OPENAI_NO_SYSTEM") == "1" and messages[0]["role"] == "system":
            first = messages[1]
            messages = [
                {"role": "user", "content": f"{messages[0]['content']}\n\n{first['content']}"},
                *messages[2:],
            ]
        # OpenAI's own API wants max_completion_tokens for reasoning models; others max_tokens.
        limit = "max_completion_tokens" if "api.openai.com" in self.base else "max_tokens"
        body: dict = {"model": self.model_id, "messages": messages, limit: 2048}
        if self.effort:
            body["reasoning_effort"] = self.effort
        body.update(self.extra)
        req = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
        )
        for attempt in range(6):
            if self.gap_s:
                self._sleep(max(0.0, self.last_call + self.gap_s - time.perf_counter()))
            self.last_call = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    out = json.loads(resp.read())
                break
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or attempt == 5:
                    sys.exit(f"row K: HTTP {e.code}: {e.read()[:500]!r}")
                self._sleep(min(60.0, 5.0 * 2**attempt))
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                # dropped connection or timeout: the network, not the request
                if attempt == 5:
                    sys.exit(f"row K: {e!r}")
                self._sleep(min(60.0, 5.0 * 2**attempt))
        usage = out.get("usage") or {}
        self.usage["input_tokens"] += int(usage.get("prompt_tokens") or 0)
        self.usage["output_tokens"] += int(usage.get("completion_tokens") or 0)
        text = str(out["choices"][0]["message"].get("content") or "")
        # Some models (Gemma 4 on the Gemini API) return their reasoning inline; keep only
        # the answer. An unclosed <thought> (ran out of tokens) is left as is, so it shows.
        return re.sub(r"(?s)^\s*<thought>.*?</thought>\s*", "", text)


def load(name: str, device: str, threads: int):
    kind = MODELS[name][1]
    if kind == "api":
        return ApiTranslator(name, device, threads)
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
        waited = getattr(models[0], "waited_s", 0.0)
        t0 = time.perf_counter()
        out = models[0].translate(text, target, context)
        return out, time.perf_counter() - t0 - (getattr(models[0], "waited_s", 0.0) - waited), []

    partial = out_dir / f"{row}.{target}.partial.jsonl"  # progress if the run stops early
    done = {}
    if partial.exists():  # --resume: keep finished sentences, carry on from the first missing
        done = {
            r["id"]: r for r in map(json.loads, partial.read_text(encoding="utf-8").splitlines())
        }
        print(f"row {row}: resuming, {len(done)} sentences already done")
    if items:
        translate(0, items[0]["text"])  # warm-up, discarded
    for i, it in enumerate(items):
        if it["id"] in done:
            records.append(done[it["id"]])
            times.append(done[it["id"]]["time_s"])
            continue
        out, dt, hops = translate(i, it["text"])
        rec = {"id": it["id"], "source": it["text"], "translation": out, "time_s": round(dt, 4)}
        if hops:
            rec.update(hop1_s=round(hops[0], 4), hop2_s=round(hops[1], 4), pivot_en=hops[2])
            hop1.append(hops[0])
            hop2.append(hops[1])
        records.append(rec)
        times.append(dt)
        with partial.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    path = out_dir / f"{row}.{target}.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    partial.unlink(missing_ok=True)
    summary = {"row": row, "target": target, "n": len(records), "file": path.name}
    if times:
        summary.update(p50_s=round(statistics.median(times), 3), p95_s=round(pct(times, 0.95), 3))
    if hop1:
        summary.update(
            hop1_p50_s=round(statistics.median(hop1), 3), hop1_p95_s=round(pct(hop1, 0.95), 3),
            hop2_p50_s=round(statistics.median(hop2), 3), hop2_p95_s=round(pct(hop2, 0.95), 3),
        )  # fmt: skip
    if hasattr(models[0], "usage"):
        summary.update(models[0].usage)  # includes the discarded warm-up call
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
    r.add_argument(
        "--resume",
        action="store_true",
        help="continue a stopped run from <row>.<target>.partial.jsonl",
    )
    args = ap.parse_args()

    if args.cmd == "prepare":
        for name in dict.fromkeys(n for row in args.rows for n in ROW_MODELS[row]):
            prepare(name)
        return 0

    out_dir = OUT_ROOT / args.run_name
    if out_dir.exists() and not args.resume:
        sys.exit(f"{out_dir} exists: pick a new --run-name (or --resume)")
    items = [
        json.loads(line)
        for line in Path(args.input).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        items = items[: args.limit]
    out_dir.mkdir(parents=True, exist_ok=args.resume)
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
        "prompt_style": PROMPT_STYLE,
        "prompt_file": PROMPT_FILE or None,
        "openai_base": os.environ.get("BL_OPENAI_BASE") or None,
        "openai_effort": os.environ.get("BL_OPENAI_EFFORT") or None,
    }  # fmt: skip
    (out_dir / "run.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
