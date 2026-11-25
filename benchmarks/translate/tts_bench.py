"""Phase 0, P0.4: speech synthesis bench (docs/translate/PHASE0.md).

Runs in its own scratch venv (~/.venvs/tts-p0: torch cu128, qwen-tts pins
transformers 4.57 which the translation venv cannot share), not the project venv.

    # 1. freeze the sentences (fixed rule, below); --lang en for English (default ru)
    python benchmarks/translate/tts_bench.py pick --mt results/translate/mt/<run>/D.ru.jsonl
    python benchmarks/translate/tts_bench.py --lang en pick --mt results/translate/mt/<run>/D.en.jsonl
    # 2. synthesize them; one run per engine / voice / device
    python benchmarks/translate/tts_bench.py run --engine supertonic --voice M1 --device cpu --threads 4
    # 3. one sentence in every voice, to choose voices by ear
    python benchmarks/translate/tts_bench.py sampler --engine silero
    # 4. listening sheet + page over finished runs
    python benchmarks/translate/tts_bench.py sheet --runs <run> <run> ...

Sentence rule (fixed before listening): from the chosen translation output,
sentences of 5-30 words in sermon order; those containing a number word or a
name other than Бог/Господь first, then the rest, at most 30/windows per
window, 30 in all. The 12-word
sentence closest to the start is the "first audio" probe (its synthesis time,
warm: no engine here streams audio inside a sentence).

Engines and licences (read 2026-11-18):
  supertonic  Supertone/supertonic-3, ONNX, CPU    code MIT, weights OpenRAIL-M
  silero      v5_cis_base + silero-stress         MIT (LICENSE_CIS), stress MIT
  qwen        Qwen3-TTS-12Hz-0.6B-CustomVoice     Apache-2.0
  kokoro      hexgrad/Kokoro-82M (English)        Apache-2.0 (read 2026-11-25)

English uses the same rule with English number words, and names are capitalized
words other than the first and God/Lord/Jesus/Christ/I.

Output: results/translate/tts/<run>/{NN.wav, results.jsonl, run.json}.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import platform
import re
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("BL_RESULTS", REPO_ROOT / "results")) / "translate" / "tts"
MODELS = Path(os.environ.get("BL_TTS_MODELS", Path.home() / ".venvs" / "models" / "tts"))
SENTENCES = OUT / "sentences.ru.jsonl"
NUMBER = re.compile(
    r"\b(перв|втор|трет|четв[её]рт|пят|шест|седьм|восьм|девят|десят|двадцат|тридцат|сорок"
    r"|пятьдесят|сто|двест|тысяч|один|одна|два|две|три|четыре)\w*|\d",
    re.IGNORECASE,
)
NOT_NAMES = {"Бог", "Бога", "Богу", "Богом", "Боже", "Господь", "Господа", "Господу", "Господом"}
# Same light normalization for every engine (Supertonic rejects „).
QUOTES = str.maketrans({"„": "«", "“": "«", "”": "»"})
NUMBER_EN = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|one|two|three|four"
    r"|five|six|seven|eight|nine|ten|eleven\w*|twelve|twelfth|twent\w*|thirt\w*|forty"
    r"|fortieth|fift\w*|sixt\w*|sevent\w*|eight\w*|ninet\w*|hundred\w*|thousand\w*)\b|\d",
    re.IGNORECASE,
)
NOT_NAMES_EN = {"God", "God's", "Lord", "Lord's", "Jesus", "Christ", "I", "I'm", "I'll"}
DEFAULT_VOICE = {"supertonic": "M1", "silero": "ru_dmitriy", "qwen": "ryan", "kokoro": "af_heart"}
LANG = "ru"  # set from --lang in main()
PROBE_WORDS = 12


# --------------------------------------------------------------------------- pick


def has_name(text: str) -> bool:
    words = [w.strip("«»„“”\"'.,:;!?()–-") for w in text.split()]
    if LANG == "en":
        return any(re.match(r"[A-Z][a-z]", w) and w not in NOT_NAMES_EN for w in words[1:])
    return any(re.match(r"[А-ЯЁ][а-яё]", w) and w not in NOT_NAMES for w in words[1:])


def has_number(text: str) -> bool:
    return bool((NUMBER_EN if LANG == "en" else NUMBER).search(text))


def pick(records: list[dict], n: int = 30) -> list[dict]:
    ok = [r for r in records if 5 <= len(r["translation"].split()) <= 30]
    first = [r for r in ok if has_number(r["translation"]) or has_name(r["translation"])]
    rest = [r for r in ok if r not in first]
    windows = list(dict.fromkeys(r["id"].rsplit("-", 1)[0] for r in records))
    per_window = -(-n // len(windows))  # spread over the windows, not the first three
    chosen: list[dict] = []
    taken = dict.fromkeys(windows, 0)
    for r in first + rest:
        w = r["id"].rsplit("-", 1)[0]
        if taken[w] < per_window and len(chosen) < n:
            chosen.append(r)
            taken[w] += 1
    order = {r["id"]: i for i, r in enumerate(records)}
    chosen.sort(key=lambda r: order[r["id"]])
    probe = next((r for r in chosen if len(r["translation"].split()) == PROBE_WORDS), None)
    if probe is None:  # make room for one
        probe = next(r for r in ok if len(r["translation"].split()) == PROBE_WORDS)
        chosen[-1] = probe
        chosen.sort(key=lambda r: order[r["id"]])
    return [
        {"no": i, "id": r["id"], "text": r["translation"], "probe": r is probe}
        for i, r in enumerate(chosen, 1)
    ]


# --------------------------------------------------------------------------- engines


class Supertonic:
    def __init__(self, voice: str, device: str, threads: int):
        if device != "cpu":
            sys.exit("supertonic: the SDK runs ONNX on CPU only")
        from supertonic import TTS

        self.tts = TTS(
            model="supertonic-3",
            model_dir=MODELS / "supertonic-3",
            intra_op_num_threads=threads or None,
        )
        self.style = self.tts.get_voice_style(voice)
        self.sr = self.tts.sample_rate

    def voices(self) -> list[str]:
        return list(self.tts.voice_style_names)

    def set_voice(self, voice: str) -> None:
        self.style = self.tts.get_voice_style(voice)

    def __call__(self, text: str):
        wav, _ = self.tts.synthesize(text, voice_style=self.style, lang=LANG)
        return wav.squeeze()


class Silero:
    SR = 48000

    def __init__(self, voice: str, device: str, threads: int):
        import torch
        from silero_stress import load_accentor

        if threads:
            torch.set_num_threads(threads)
        imp = torch.package.PackageImporter(str(MODELS / "v5_cis_base.pt"))
        self.model = imp.load_pickle("tts_models", "model")
        self.model.to(torch.device(device))
        self.accentor = load_accentor()
        self.voice = voice
        self.sr = self.SR

    def voices(self) -> list[str]:
        return [s for s in self.model.speakers if s.startswith("ru_")]

    def set_voice(self, voice: str) -> None:
        self.voice = voice

    def __call__(self, text: str):
        # v5_cis_base needs a stress mark on every Russian word (README); the
        # stress model's time is part of synthesis time.
        audio = self.model.apply_tts(
            text=self.accentor(text), speaker=self.voice, sample_rate=self.SR
        )
        return audio.cpu().numpy()


class Qwen:
    def __init__(self, voice: str, device: str, threads: int):
        import torch
        from qwen_tts import Qwen3TTSModel

        if threads:
            torch.set_num_threads(threads)
        self.model = Qwen3TTSModel.from_pretrained(
            "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
            device_map="cuda:0" if device == "cuda" else "cpu",
            dtype=torch.bfloat16 if device == "cuda" else torch.float32,
        )
        self.voice = voice
        self.sr = 24000

    def voices(self) -> list[str]:
        return sorted(self.model.model.get_supported_speakers())

    def set_voice(self, voice: str) -> None:
        self.voice = voice

    def __call__(self, text: str):
        wavs, self.sr = self.model.generate_custom_voice(
            text=text, speaker=self.voice, language="English" if LANG == "en" else "Russian"
        )
        return wavs[0]


class Kokoro:
    """Kokoro-82M; American (a*) or British (b*) English voices."""

    SR = 24000
    REPO = "hexgrad/Kokoro-82M"

    def __init__(self, voice: str, device: str, threads: int):
        import torch
        from kokoro import KModel, KPipeline

        if LANG != "en":
            sys.exit("kokoro: English only here")
        if threads:
            torch.set_num_threads(threads)
        self.model = KModel(repo_id=self.REPO).to(device).eval()
        self.pipes = {
            code: KPipeline(lang_code=code, repo_id=self.REPO, model=self.model) for code in "ab"
        }
        self.voice = voice
        self.sr = self.SR

    def voices(self) -> list[str]:
        from huggingface_hub import list_repo_files

        files = list_repo_files(self.REPO)
        return sorted(Path(f).stem for f in files if f.startswith("voices/") and f[7] in "ab")

    def set_voice(self, voice: str) -> None:
        self.voice = voice

    def __call__(self, text: str):
        import numpy as np

        parts = [r.audio.cpu().numpy() for r in self.pipes[self.voice[0]](text, voice=self.voice)]
        return np.concatenate(parts)


ENGINES: dict[str, Any] = {
    "supertonic": Supertonic, "silero": Silero, "qwen": Qwen, "kokoro": Kokoro,
}  # fmt: skip


# --------------------------------------------------------------------------- measuring


class PeakMemory:
    """Peak process RSS (sampled) and, for torch engines, peak reserved VRAM."""

    def __init__(self) -> None:
        import psutil

        self.proc = psutil.Process()
        self.peak_rss = self.proc.memory_info().rss
        self._stop = False
        self._t = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop:
            self.peak_rss = max(self.peak_rss, self.proc.memory_info().rss)
            time.sleep(0.05)

    def __enter__(self) -> PeakMemory:
        self._t.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop = True
        self._t.join()


def peak_vram_mib() -> float | None:
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available() or not torch.cuda.is_initialized():
        return None
    return float(round(torch.cuda.max_memory_reserved() / 2**20))


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def write_wav(path: Path, audio, sr: int) -> None:
    import soundfile as sf

    sf.write(str(path), audio, sr, subtype="PCM_16")


# --------------------------------------------------------------------------- commands


def cmd_pick(args: argparse.Namespace) -> int:
    if SENTENCES.exists() and not args.force:
        sys.exit(f"{SENTENCES} is frozen; pass --force (and say why) to redo")
    records = [json.loads(x) for x in Path(args.mt).read_text(encoding="utf-8").splitlines() if x]
    chosen = pick(records)
    OUT.mkdir(parents=True, exist_ok=True)
    SENTENCES.write_text(
        "".join(
            json.dumps({**c, "source": str(args.mt)}, ensure_ascii=False) + "\n" for c in chosen
        ),
        encoding="utf-8",
    )
    print(f"{len(chosen)} sentences -> {SENTENCES}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    items = [json.loads(x) for x in SENTENCES.read_text(encoding="utf-8").splitlines() if x]
    for it in items:
        it["text"] = it["text"].translate(QUOTES)
    if args.limit:
        items = items[: args.limit]
    voice = args.voice or DEFAULT_VOICE[args.engine]
    name = args.run_name or f"{args.engine}-{voice}-{args.device}{args.threads or ''}"
    out = OUT / name
    if out.exists():
        sys.exit(f"{out} exists: pick a new --run-name")
    out.mkdir(parents=True)
    with PeakMemory() as mem:
        t0 = time.perf_counter()
        engine = ENGINES[args.engine](voice, args.device, args.threads)
        load_s = time.perf_counter() - t0
        engine(items[0]["text"])  # warm-up, discarded
        rows = []
        for it in items:
            t = time.perf_counter()
            audio = engine(it["text"])
            dt = time.perf_counter() - t
            dur = len(audio) / engine.sr
            write_wav(out / f"{it['no']:02d}.wav", audio, engine.sr)
            rows.append({**it, "synth_s": round(dt, 3), "audio_s": round(dur, 3),
                         "rtf": round(dt / dur, 4)})  # fmt: skip
            print(f"{it['no']:2d} rtf {dt / dur:.3f} ({dt:.2f} s for {dur:.1f} s)", flush=True)
    (out / "results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    rtfs = [r["rtf"] for r in rows]
    probe = next((r for r in rows if r["probe"]), None)
    meta = {
        "engine": args.engine, "voice": voice, "device": args.device, "threads": args.threads,
        "host": platform.node(), "n": len(rows), "sample_rate": engine.sr,
        "load_s": round(load_s, 2),
        "rtf_total": round(sum(r["synth_s"] for r in rows) / sum(r["audio_s"] for r in rows), 4),
        "rtf_p50": round(statistics.median(rtfs), 4), "rtf_p95": round(pct(rtfs, 0.95), 4),
        "first_audio_12w_s": probe["synth_s"] if probe else None,
        "peak_rss_mib": round(mem.peak_rss / 2**20), "peak_vram_reserved_mib": peak_vram_mib(),
    }  # fmt: skip
    (out / "run.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


def cmd_sampler(args: argparse.Namespace) -> int:
    items = [json.loads(x) for x in SENTENCES.read_text(encoding="utf-8").splitlines() if x]
    probe = next(it for it in items if it["probe"])
    engine = ENGINES[args.engine](DEFAULT_VOICE[args.engine], args.device, args.threads)
    out = OUT / f"sampler-{args.engine}"
    out.mkdir(parents=True, exist_ok=True)
    for v in engine.voices():
        engine.set_voice(v)
        write_wav(out / f"{v}.wav", engine(probe["text"]), engine.sr)
        print(v, flush=True)
    voices = "".join(
        f"<p>{html.escape(v)}<br><audio controls preload='none' src='{v}.wav'></audio></p>"
        for v in engine.voices()
    )
    (out / "index.html").write_text(
        f"<!doctype html><meta charset='utf-8'><title>{args.engine} voices</title>"
        f"<p>{html.escape(probe['text'])}</p>{voices}",
        encoding="utf-8",
    )
    print(f"-> {out / 'index.html'}")
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    runs = [OUT / r for r in args.runs]
    items = [json.loads(x) for x in SENTENCES.read_text(encoding="utf-8").splitlines() if x]
    path = OUT / f"{args.name}.csv"
    if path.exists():
        sys.exit(f"{path} exists")
    cols = [
        "sentence_no",
        "run",
        "clip",
        "text",
        "voice_ok",
        "stress_errors",
        "name_errors",
        "note",
    ]
    blocks = []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for it in items:
            players = []
            for run in runs:
                clip = f"{run.name}/{it['no']:02d}.wav"
                if not (OUT / clip).exists():
                    continue
                w.writerow({"sentence_no": it["no"], "run": run.name, "clip": clip,
                            "text": it["text"], "voice_ok": "", "stress_errors": "",
                            "name_errors": "", "note": ""})  # fmt: skip
                players.append(
                    f"<div>{html.escape(run.name)}<br>"
                    f"<audio controls preload='none' src='{clip}'></audio></div>"
                )
            blocks.append(
                f"<section><h3>{it['no']}. {html.escape(it['text'])}</h3>{''.join(players)}</section>"
            )
    page = OUT / f"{args.name}.html"
    page.write_text(
        "<!doctype html><meta charset='utf-8'><title>Listening sheet</title>"
        "<style>body{font-family:sans-serif;max-width:900px;margin:auto;padding:16px}"
        "section{border-bottom:1px solid #ccc;padding:8px 0}"
        "section div{display:inline-block;margin:4px 16px 4px 0}</style>" + "".join(blocks),
        encoding="utf-8",
    )
    print(f"-> {path}\n-> {page}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", choices=["ru", "en"], default="ru")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick")
    p.add_argument("--mt", required=True, help="translation output JSONL (id, translation)")
    p.add_argument("--force", action="store_true")
    for name in ("run", "sampler"):
        r = sub.add_parser(name)
        r.add_argument("--engine", choices=list(ENGINES), required=True)
        r.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
        r.add_argument("--threads", type=int, default=0)
        if name == "run":
            r.add_argument("--voice")
            r.add_argument("--run-name")
            r.add_argument("--limit", type=int, default=0)
    s = sub.add_parser("sheet")
    s.add_argument("--runs", nargs="+", required=True)
    s.add_argument("--name", default="listen-ru")
    args = ap.parse_args()
    global LANG, SENTENCES
    LANG = args.lang
    SENTENCES = OUT / f"sentences.{LANG}.jsonl"
    if LANG == "en" and args.cmd == "sheet" and args.name == "listen-ru":
        args.name = "listen-en"
    return {"pick": cmd_pick, "run": cmd_run, "sampler": cmd_sampler, "sheet": cmd_sheet}[args.cmd](
        args
    )


if __name__ == "__main__":
    raise SystemExit(main())
