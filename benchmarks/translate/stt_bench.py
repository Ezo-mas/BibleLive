"""Phase 0, P0.2: speech recognition and segmentation on the six corrected windows.

Project venv. For each window (results/translate/clips/<id>.wav, reference
results/translate/drafts/<id>.txt, corrected by the owner) and each segmentation
setting, the clip's 30 ms frames go through the live WebRTC VAD and
``audio.segmenter.Segmenter`` offline; every segment is transcribed by one system.

    python benchmarks/translate/stt_bench.py run --system kmynas-gpu --name p02 [--s2]
    python benchmarks/translate/stt_bench.py report --name p02

Settings: ``default`` (BibleLive: end of speech 1.5 s, soft 8 s, hard 12 s) and
``t05`` / ``t06`` / ``t08`` (end of speech 0.5 / 0.6 / 0.8 s, soft 4 s, hard 7 s).
``--s2`` (Kmynas GPU): on the default segmentation, the open segment is transcribed
again every 1.5 s of clock time; a word is committed when two consecutive passes
agree on the prefix up to it, the rest when the segment closes.

Measures, per system and setting:
- WER: lower case, punctuation removed, ``(?)`` marks dropped, <skip> lines out of
  the reference and segments centred in them out of the hypothesis. Numbers are not
  normalized (Kmynas and Canary write words, as the reference does; Whisper may write
  digits).
- Sentence-boundary F1: a hypothesis word ending in . ? ! … against the corrected
  punctuation, matched one to one within one word after alignment.
- RTF: transcription time / segment audio; peak memory: video (nvidia-smi, minus
  the reading before the model loaded) and the process's peak working set.
- Text delay: for every reference word matched to a hypothesis word, the time from
  the word's end to the moment its text is available. Word times are linear
  interpolation inside each corrected line (no forced alignment). Available = the
  segment's close time, then a single worker transcribing in order (measured time);
  for S2 the commit time of the word.
"""

from __future__ import annotations

import argparse
import ctypes
import difflib
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "backend"))

from build_sermon150 import WINDOW_IDS, norm, parse  # noqa: E402

ROOT = REPO / "results" / "translate"
OUT = ROOT / "stt"
MODELS = Path.home() / ".venvs" / "models"
GGUF = {
    "kmynas": MODELS / "kmynas-v4" / "kmynas-v4" / "kmynas-v4-Q8_0.gguf",
    "canary": MODELS / "canary-gguf" / "canary-1b-v2-Q8_0.gguf",
}
SYSTEMS = {  # name -> (engine, device)
    "kmynas-gpu": ("kmynas", "cuda"),
    "kmynas-cpu": ("kmynas", "cpu"),
    "canary-gpu": ("canary", "cuda"),
    "whisper-gpu": ("whisper", "cuda"),
}
SETTINGS = {  # name -> (end of speech, soft, hard)
    "default": (1.5, 8.0, 12.0),
    "t05": (0.5, 4.0, 7.0),
    "t06": (0.6, 4.0, 7.0),
    "t08": (0.8, 4.0, 7.0),
}
FRAME_S = 0.03
S2_EVERY_S = 1.5
SENT_END = re.compile(r"[.?!…][\"“”„»)\]’']*$")


# --------------------------------------------------------------------------- engines


def load_engine(system: str, threads: int):
    engine, device = SYSTEMS[system]
    if engine == "whisper":
        from audio.stt import SpeechToText

        stt = SpeechToText("large-v3-turbo", device, "lt", "float16", cpu_threads=threads)
        return lambda pcm: stt.transcribe_utterance(pcm, "lt")
    import numpy as np

    from audio.stt_gguf import GgufSpeechToText

    m = GgufSpeechToText(engine, str(GGUF[engine]), device, threads)

    def run(pcm: bytes):
        return m.transcribe(np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768, "lt")

    return run


def vram_mib() -> int:
    r = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
    )
    return int(r.stdout.split()[0])


def peak_working_set_mib() -> int:
    if sys.platform != "win32":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024

    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]  # fmt: skip

    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = ctypes.c_void_p  # a pseudo-handle (-1), not an int
    k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
    k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return int(pmc.PeakWorkingSetSize) // 2**20


class VramPeak(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self.stop = 0, threading.Event()

    def run(self):
        while not self.stop.is_set():
            self.peak = max(self.peak, vram_mib())
            self.stop.wait(0.5)


# --------------------------------------------------------------------------- segmentation


def read_clip(window: str) -> tuple[float, list[bytes]]:
    headers, _ = parse(ROOT / "drafts" / f"{window}.txt")
    start = float(headers["range_s"].split("-")[0])
    with wave.open(str(ROOT / "clips" / f"{window}.wav")) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        data = w.readframes(w.getnframes())
    n = int(16000 * FRAME_S) * 2
    return start, [data[i : i + n] for i in range(0, len(data) - n + 1, n)]


def segmenter(setting: str):
    from audio.config import AudioPipelineConfig
    from audio.segmenter import Segmenter
    from audio.vad import create_vad

    cfg = AudioPipelineConfig()
    cfg.end_of_speech_s, cfg.soft_utterance_s, cfg.max_utterance_s = SETTINGS[setting]
    vad = create_vad(cfg.vad, aggressiveness=cfg.vad_aggressiveness)
    return Segmenter.from_config(vad.is_speech, cfg)


def transcribe_window(window: str, setting: str, stt, s2: bool) -> list[dict]:
    """Segments in order: t0/t1 speech span, close (s, source-file time), ms, text, and
    for S2 the passes (time the snapshot was taken, ms, text)."""
    start, frames = read_clip(window)
    seg = segmenter(setting)
    out: list[dict] = []
    passes: list[dict] = []
    onset, next_pass = None, 0.0
    for i, frame in enumerate(frames):
        clock = start + (i + 1) * FRAME_S
        ev = seg.push(frame)
        if ev is not None and ev.kind == "start":
            onset = clock - ev.onset_frames * FRAME_S
            passes, next_pass = [], onset + S2_EVERY_S
        elif s2 and seg.in_utterance and onset is not None and clock >= next_pass:
            t = time.perf_counter()
            tr = stt(b"".join(seg.speech_frames))
            passes.append(
                {
                    "at": round(clock, 3),
                    "ms": (time.perf_counter() - t) * 1000,
                    "text": tr.text or "",
                }
            )
            next_pass += S2_EVERY_S
        if ev is not None and ev.kind == "end":
            t = time.perf_counter()
            tr = stt(b"".join(ev.frames))
            ms = (time.perf_counter() - t) * 1000
            out.append(
                {
                    "t0": round(onset if onset is not None else clock, 3),
                    "t1": round(clock - ev.trailing_silent_frames * FRAME_S, 3),
                    "close": round(clock, 3),
                    "audio_s": round(len(ev.frames) * FRAME_S, 2),
                    "reason": ev.reason,
                    "ms": round(ms, 1),
                    "text": tr.text or "",
                    "low_conf": tr.low_confidence_reason,
                    **({"passes": passes} if s2 else {}),
                }
            )
            onset = None
    return out


def cmd_run(args: argparse.Namespace) -> int:
    out = OUT / args.name / (args.system + ("-s2" if args.s2 else ""))
    if out.exists():
        sys.exit(f"{out} exists")
    out.mkdir(parents=True)
    settings = ["default"] if args.s2 else args.settings
    before = vram_mib()
    peak = VramPeak()
    peak.start()
    t = time.perf_counter()
    stt = load_engine(args.system, args.threads)
    load_s = time.perf_counter() - t
    stt(b"\0\0" * 16000)  # warm-up
    for setting in settings:
        with (out / f"{setting}.jsonl").open("w", encoding="utf-8") as f:
            for window in WINDOW_IDS:
                for s in transcribe_window(window, setting, stt, args.s2):
                    f.write(json.dumps({"window": window, **s}, ensure_ascii=False) + "\n")
        print(f"{args.system} {setting} done", flush=True)
    peak.stop.set()
    peak.join()
    meta = {
        "system": args.system, "s2": args.s2, "threads": args.threads, "settings": settings,
        "load_s": round(load_s, 1), "vram_before_mib": before, "vram_peak_mib": peak.peak,
        "vram_added_mib": peak.peak - before, "peak_working_set_mib": peak_working_set_mib(),
    }  # fmt: skip
    (out / "run.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps(meta))
    return 0


# --------------------------------------------------------------------------- scoring


def ref_words(window: str) -> tuple[list[dict], list[tuple[float, float]]]:
    """Reference words with end times and sentence ends; spans of <skip> lines."""
    _, lines = parse(ROOT / "drafts" / f"{window}.txt")
    words, skips = [], []
    for a, b, text in lines:
        if text == "<skip>":
            skips.append((a, b))
            continue
        toks = text.split()
        for k, tok in enumerate(toks):
            n = norm(tok)
            if not n:
                continue
            words.append(
                {
                    "n": n,
                    "end": a + (b - a) * (k + 1) / len(toks),
                    "sent_end": bool(SENT_END.search(tok)) and not tok.endswith("(?)"),
                }
            )
    return words, skips


def hyp_words(segs: list[dict], skips: list[tuple[float, float]], s2: bool) -> list[dict]:
    """Hypothesis words in order with the time each became available."""
    words, free = [], 0.0
    for s in segs:
        mid = (s["t0"] + s["t1"]) / 2
        if any(a <= mid <= b for a, b in skips):
            continue
        if s2:
            committed: list[tuple[str, float]] = []
            prev: list[str] = []
            for p in s.get("passes", []):
                free = max(free, p["at"]) + p["ms"] / 1000
                cur = p["text"].split()
                agree = 0
                while agree < min(len(cur), len(prev)) and norm(cur[agree]) == norm(prev[agree]):
                    agree += 1
                for tok in cur[len(committed) : agree]:
                    committed.append((tok, free))
                prev = cur
            free = max(free, s["close"]) + s["ms"] / 1000
            final = s["text"].split()
            toks = committed + [(tok, free) for tok in final[len(committed) :]]
        else:
            free = max(free, s["close"]) + s["ms"] / 1000
            toks = [(tok, free) for tok in s["text"].split()]
        for tok, at in toks:
            n = norm(tok)
            if n:
                words.append({"n": n, "at": at, "sent_end": bool(SENT_END.search(tok))})
    return words


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def score_window(ref: list[dict], hyp: list[dict]) -> dict:
    r, h = [w["n"] for w in ref], [w["n"] for w in hyp]
    errors = edit_distance(r, h)
    sm = difflib.SequenceMatcher(None, r, h, autojunk=False)
    ref_to_hyp: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("equal", "replace"):
            for k in range(min(i2 - i1, j2 - j1)):
                ref_to_hyp[i1 + k] = j1 + k
    delays = [hyp[j]["at"] - ref[i]["end"] for i, j in ref_to_hyp.items()]
    hyp_to_ref = {j: i for i, j in ref_to_hyp.items()}
    ref_b = {i for i, w in enumerate(ref) if w["sent_end"]}
    hyp_b = [hyp_to_ref[j] for j, w in enumerate(hyp) if w["sent_end"] and j in hyp_to_ref]
    matched, used = 0, set()
    for i in hyp_b:
        for c in (i, i - 1, i + 1):
            if c in ref_b and c not in used:
                used.add(c)
                matched += 1
                break
    n_hyp_b = sum(1 for w in hyp if w["sent_end"])
    return {"ref_words": len(r), "errors": errors, "delays": delays, "tp": matched,
            "ref_b": len(ref_b), "hyp_b": n_hyp_b}  # fmt: skip


def pct(v: list[float], p: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def cmd_report(args: argparse.Namespace) -> int:
    rows = []
    for d in sorted((OUT / args.name).iterdir()):
        meta = json.loads((d / "run.json").read_text(encoding="utf-8"))
        for setting in meta["settings"]:
            segs = [
                json.loads(x)
                for x in (d / f"{setting}.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            tot: dict[str, Any] = {
                "ref_words": 0,
                "errors": 0,
                "delays": [],
                "tp": 0,
                "ref_b": 0,
                "hyp_b": 0,
            }
            for window in WINDOW_IDS:
                ref, skips = ref_words(window)
                hyp = hyp_words([s for s in segs if s["window"] == window], skips, meta["s2"])
                sc = score_window(ref, hyp)
                for k in tot:
                    tot[k] += sc[k]
            ms = sum(s["ms"] + sum(p["ms"] for p in s.get("passes", [])) for s in segs)
            audio = sum(s["audio_s"] for s in segs)
            p, r = tot["tp"] / max(1, tot["hyp_b"]), tot["tp"] / max(1, tot["ref_b"])
            rows.append({
                "system": d.name, "setting": setting, "segments": len(segs),
                "seg_len_p50": round(statistics.median(s["audio_s"] for s in segs), 2),
                "wer": round(100 * tot["errors"] / tot["ref_words"], 2),
                "boundary_p": round(p, 3), "boundary_r": round(r, 3),
                "boundary_f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
                "rtf": round(ms / 1000 / audio, 4),
                "delay_p50": round(statistics.median(tot["delays"]), 2),
                "delay_p95": round(pct(tot["delays"], 0.95), 2),
                "matched_words": len(tot["delays"]), "ref_words": tot["ref_words"],
                "vram_added_mib": meta["vram_added_mib"], "peak_ws_mib": meta["peak_working_set_mib"],
            })  # fmt: skip
    (OUT / args.name / "report.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    cols = ["system", "setting", "segments", "seg_len_p50", "wer", "boundary_f1", "boundary_p",
            "boundary_r", "rtf", "delay_p50", "delay_p95", "vram_added_mib", "peak_ws_mib"]  # fmt: skip
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(str(r[c]) for c in cols))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--system", required=True, choices=list(SYSTEMS))
    r.add_argument("--name", required=True)
    r.add_argument("--settings", nargs="+", default=list(SETTINGS), choices=list(SETTINGS))
    r.add_argument("--threads", type=int, default=0)
    r.add_argument("--s2", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("--name", required=True)
    args = ap.parse_args()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    return cmd_run(args) if args.cmd == "run" else cmd_report(args)


if __name__ == "__main__":
    raise SystemExit(main())
