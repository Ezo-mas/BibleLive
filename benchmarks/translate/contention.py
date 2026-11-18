"""Phase 0, P0.6: BibleLive and Translate running together on one machine, in real time.

Three roles, one file:

- ``run`` (scratch venv ~/.venvs/tts-p0, which has psutil, Silero and CTranslate2):
  the orchestrator. Starts the BibleLive server (port 8000), the optional
  llama-server (port 8082, our own process only), the hub, then the pipeline;
  samples video memory (nvidia-smi, minus the idle reading taken before anything
  starts) and the RAM of every process it started; writes the summary.
- ``pipeline`` (project venv): the real BibleLive pipeline (``audio.cli``). Patched
  in this process only:
    * the capture's ffmpeg reads ``--audio`` from ``--start`` for ``--dur`` seconds
      at real-time speed (``-re``), followed by silence, instead of a microphone;
    * ``--canary-device`` moves Canary to another device (option 1: Kmynas on the
      GPU, Canary on the CPU), which the pipeline cannot do itself;
    * the queue time and done time of every utterance are written to timing.jsonl;
    * with ``--tap``, a second segmenter with translation settings (PHASE0 P0.2's
      proposal: end of speech 0.6 s, soft 4 s, hard 7 s; P0.2 has not chosen yet)
      reads the same frames on its own thread and transcribes its segments with the
      pipeline's own Kmynas instance (one front end, plan T2/T3: a per-model lock
      makes the two streams queue for the model as they would in one process), then
      posts each segment to the hub over loopback HTTP.
- ``hub`` (scratch venv): splits segments into sentences (MVP commit policy T4: one
  sentence per unit; a trailing fragment of a hard cut waits up to 2 s for the next
  segment), translates them (row D through llama-server, or row B with CTranslate2
  on the CPU) with two sentences of context, synthesizes each with Silero, and
  simulates one listener's player (clips play back to back).

Delays are measured from the wall time the segment's last speech frame arrived:
text delay = translation done, audio start = the simulated player starts the clip.
They are per sentence from its segment's end, so earlier words in a segment waited
longer by up to the segment's length (reported as ``seg_len_s``).

    # GPU tier, option 1, without and with Translate (20 min of 05-31)
    ~/.venvs/tts-p0/Scripts/python.exe benchmarks/translate/contention.py run \
        --name gpu-base --stt kmynas+canary --device cuda --canary-device cpu
    ~/.venvs/tts-p0/Scripts/python.exe benchmarks/translate/contention.py run \
        --name gpu-tr --stt kmynas+canary --device cuda --canary-device cpu \
        --translate D --baseline gpu-base
    # CPU tier
    ... run --name cpu-base --stt kmynas --device cpu --cpu-threads 3
    ... run --name cpu-tr --stt kmynas --device cpu --cpu-threads 3 --translate B \
        --mt-threads 3 --baseline cpu-base

Outputs: results/translate/contention/<name>/ (never overwritten).
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO / "results" / "translate" / "contention"
PROJECT_PY = REPO / ".venv" / "Scripts" / "python.exe"
MODELS = Path.home() / ".venvs" / "models"
KMYNAS = MODELS / "kmynas-v4" / "kmynas-v4" / "kmynas-v4-Q8_0.gguf"
CANARY = MODELS / "canary-gguf" / "canary-1b-v2-Q8_0.gguf"
EUROLLM = MODELS / "translate" / "eurollm-9b" / "EuroLLM-9B-Instruct-IQ3_XS.gguf"
LLAMA_SERVER = Path(r"E:\LocalLLM\llama.cpp\b12368\llama-server.exe")
LLAMA_PORT = 8082  # never 8081: the owner's own llama-server
HUB_PORT = 8090
SERVER_PORT = 8000
DEFAULT_AUDIO = REPO / "bench_audio" / "benchmark_audio_05-31-norm.wav"
FRAME_S = 0.03
TAIL_S = 25.0  # after the clip: silence, so the last utterances close and drain


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 3)


def stats(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": round(statistics.median(values), 3) if values else None,
        "p95": pct(values, 0.95),
        "max": round(max(values), 3) if values else None,
    }


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def http_json(url: str, body: dict | None = None, timeout: float = 5.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return dict(json.loads(resp.read() or b"{}"))


def port_busy(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


# --------------------------------------------------------------------------- pipeline role


def cmd_pipeline(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(REPO / "backend"))
    import numpy as np

    from audio import session_log as session_log_mod
    from audio import stt_gguf
    from audio.config import AudioPipelineConfig
    from audio.segmenter import Segmenter
    from audio.vad import create_vad

    out = Path(args.out)
    timing = (out / "timing.jsonl").open("a", encoding="utf-8")
    timing_lock = threading.Lock()

    def log(rec: dict) -> None:
        with timing_lock:
            timing.write(json.dumps(rec, ensure_ascii=False) + "\n")
            timing.flush()

    # 1. ffmpeg reads the recording at real-time speed.
    # A subclass, not a function: the capture module annotates with Popen[bytes].
    class FilePopen(subprocess.Popen):
        def __init__(self, argv, *a, **kw):
            if isinstance(argv, list) and argv and argv[0] == "ffmpeg" and argv[-1] == "-":
                argv = file_argv()
                log({"type": "ffmpeg", "wall": time.time(), "argv": argv})
            super().__init__(argv, *a, **kw)

    def file_argv() -> list[str]:
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-re", "-ss", str(args.start), "-t", str(args.dur), "-i", args.audio,
            "-re", "-f", "lavfi", "-t", str(TAIL_S + 60), "-i", "anullsrc=r=16000:cl=mono",
            "-filter_complex",
            "[0:a]aresample=16000,aformat=channel_layouts=mono[a];[a][1:a]concat=n=2:v=0:a=1",
            "-ac", "1", "-ar", "16000", "-f", "s16le", "-",
        ]  # fmt: skip

    subprocess.Popen = FilePopen  # type: ignore[misc, assignment]

    # 2. Canary's device; one lock per model, shared by the pipeline and the tap.
    models: dict[str, Any] = {}
    real_init = stt_gguf.GgufSpeechToText.__init__
    real_transcribe = stt_gguf.GgufSpeechToText.transcribe

    def init(self, name, model_path, device="cuda", cpu_threads=0):
        if name == "canary" and args.canary_device:
            device = args.canary_device
            cpu_threads = args.canary_threads or cpu_threads
        real_init(self, name, model_path, device, cpu_threads)
        self._bench_lock = threading.Lock()
        models[name] = self
        log({"type": "model", "name": name, "device": device, "threads": cpu_threads})

    def transcribe(self, audio, language):
        with self._bench_lock:
            return real_transcribe(self, audio, language)

    stt_gguf.GgufSpeechToText.__init__ = init  # type: ignore[method-assign]
    stt_gguf.GgufSpeechToText.transcribe = transcribe  # type: ignore[method-assign]

    # 3. Queue and done times of BibleLive's utterances; frames to the tap.
    SessionLog = session_log_mod.SessionLog
    queued: dict[int, float] = {}
    real_queued, real_utt, real_frame = (
        SessionLog.utterance_queued, SessionLog.utterance, SessionLog.write_frame,
    )  # fmt: skip
    tap_q: queue.Queue = queue.Queue()

    def utterance_queued(self, frames, trailing):
        queued[id(frames)] = time.time()
        real_queued(self, frames, trailing)

    def utterance(self, frames, language, result, references):
        done = time.time()
        span = self._pending.get(id(frames))
        real_utt(self, frames, language, result, references)
        ms = []
        r = result
        while r is not None:
            ms.append(round(r.duration_ms))
            r = r.alternate
        log(
            {
                "type": "utterance",
                "start_s": round(span[0], 2) if span else None,
                "end_s": round(span[1], 2) if span else None,
                "queued_wall": queued.pop(id(frames), None),
                "done_wall": done,
                "stt_ms": ms,
                "references": [session_log_mod._reference_json(x) for x in references],
            }
        )

    def write_frame(self, frame):
        if args.tap:
            tap_q.put((frame, time.time()))
        real_frame(self, frame)

    SessionLog.utterance_queued = utterance_queued  # type: ignore[method-assign]
    SessionLog.utterance = utterance  # type: ignore[method-assign]
    SessionLog.write_frame = write_frame  # type: ignore[method-assign]

    # 4. The tap: translation segmenter -> Kmynas (shared) -> hub.
    def tap() -> None:
        cfg = AudioPipelineConfig()
        cfg.end_of_speech_s, cfg.soft_utterance_s, cfg.max_utterance_s = (
            args.tap_eos,
            args.tap_soft,
            args.tap_hard,
        )
        vad = create_vad(cfg.vad, aggressiveness=cfg.vad_aggressiveness)
        seg = Segmenter.from_config(vad.is_speech, cfg)
        n, seq = 0, 0
        while True:
            item = tap_q.get()
            if item is None:
                return
            frame, wall = item
            n += 1
            ev = seg.push(frame)
            if ev is None or ev.kind != "end":
                continue
            t1 = (n - ev.trailing_silent_frames) * FRAME_S
            end_wall = wall - ev.trailing_silent_frames * FRAME_S
            pcm = np.frombuffer(b"".join(ev.frames), dtype=np.int16).astype(np.float32) / 32768
            model = models.get("kmynas")
            if model is None:
                continue
            t_stt = time.time()
            tr = model.transcribe(pcm, "lt")
            done = time.time()
            seq += 1
            body = {
                "seq": seq, "t1": round(t1, 2), "audio_s": round(len(ev.frames) * FRAME_S, 2),
                "reason": ev.reason, "text": tr.text or "", "low_conf": tr.low_confidence_reason,
                "end_wall": end_wall, "stt_start_wall": t_stt, "stt_done_wall": done,
            }  # fmt: skip
            log({"type": "tap", **body})
            try:
                http_json(f"http://127.0.0.1:{args.hub_port}/ingest/segment", body)
            except Exception as e:  # fire-and-forget, like the planned tap
                log({"type": "tap_error", "seq": seq, "error": repr(e)})

    if args.tap:
        threading.Thread(target=tap, name="tap", daemon=True).start()

    # 5. Stop after the clip and its tail.
    import _thread

    threading.Timer(args.dur + TAIL_S, _thread.interrupt_main).start()
    os.chdir(REPO)
    from audio import cli

    sys.argv = [
        "audio.cli", "--language", "lt", "--stt", args.stt, "--device", args.device,
        "--kmynas-model", str(KMYNAS), "--canary-model", str(CANARY),
        "--input-device", "contention-file", "--no-control",
        "--session-log-dir", str(out / "session"),
        "--server", f"http://127.0.0.1:{SERVER_PORT}",
    ] + (["--cpu-threads", str(args.cpu_threads)] if args.cpu_threads else [])  # fmt: skip
    log({"type": "start", "wall": time.time(), "argv": sys.argv})
    cli.main()
    tap_q.put(None)
    log({"type": "stop", "wall": time.time()})
    return 0


# --------------------------------------------------------------------------- hub role

SENT_END = re.compile(r"(?<=[.!?…])\s+")


def cmd_hub(args: argparse.Namespace) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    out = Path(args.out)
    seg_q: queue.Queue = queue.Queue()
    mt_q: queue.Queue = queue.Queue()
    tts_q: queue.Queue = queue.Queue()
    units_f = (out / "units.jsonl").open("a", encoding="utf-8")
    busy = {"commit": 0, "mt": 0, "tts": 0}
    state = {"ready": False, "finishing": False}

    if args.translate == "D":
        os.environ["BL_LLAMA_SERVER"] = f"http://127.0.0.1:{LLAMA_PORT}"
        from mt_bench import LlamaTranslator

        translator = LlamaTranslator("eurollm-9b", "cuda", 0)
    else:
        from mt_bench import CT2Translator

        translator = CT2Translator("opus-bat-zle", args.mt_device, args.mt_threads)
    from tts_bench import Silero

    tts = Silero(args.voice, "cpu", args.tts_threads)
    translator.translate("Labas rytas.", "ru", [])  # warm-up
    tts("Доброе утро.")

    def commit() -> None:
        held: dict | None = None  # trailing fragment of a hard cut
        uid = 0
        while True:
            try:
                seg = seg_q.get(timeout=0.1)
            except queue.Empty:
                seg = None
            if seg is None and held is not None and time.time() - held["held_at"] > 2.0:
                parts, meta, held = [held["text"]], held["meta"], None
            elif seg is None:
                if state["finishing"] and seg_q.empty():
                    mt_q.put(None)
                    return
                continue
            else:
                busy["commit"] += 1
                if seg.get("low_conf") or not seg["text"].strip():
                    busy["commit"] -= 1
                    continue
                text = seg["text"].strip()
                if held is not None:
                    text = held["text"] + " " + text
                    held = None
                parts = [p for p in SENT_END.split(text) if p.strip()]
                meta = seg
                if seg["reason"] == "cap" and parts and not re.search(r"[.!?…]$", parts[-1]):
                    held = {"text": parts.pop(), "meta": seg, "held_at": time.time()}
                busy["commit"] -= 1
            for p in parts:
                uid += 1
                mt_q.put({"uid": uid, "text": p, "commit_wall": time.time(), **{
                    k: meta[k] for k in ("seq", "t1", "audio_s", "end_wall", "stt_done_wall")
                }})  # fmt: skip

    def translate() -> None:
        ctx: list[tuple[str, str]] = []
        while True:
            u = mt_q.get()
            if u is None:
                tts_q.put(None)
                return
            busy["mt"] += 1
            t0 = time.time()
            u["translation"] = translator.translate(u["text"], "ru", ctx[-2:])
            u["mt_start_wall"], u["mt_done_wall"] = t0, time.time()
            ctx.append((u["text"], u["translation"]))
            tts_q.put(u)
            busy["mt"] -= 1

    def synth() -> None:
        play_end = 0.0
        while True:
            u = tts_q.get()
            if u is None:
                return
            busy["tts"] += 1
            t0 = time.time()
            wav = tts(u["translation"].translate(str.maketrans({"„": "«", "“": "«", "”": "»"})))
            done = time.time()
            audio_s = len(wav) / tts.sr
            play_start = max(done, play_end)
            play_end = play_start + audio_s
            u.update(
                tts_start_wall=t0, tts_done_wall=done, tts_audio_s=round(audio_s, 2),
                play_start_wall=play_start, play_end_wall=play_end,
                text_delay_s=round(u["mt_done_wall"] - u["end_wall"], 3),
                audio_start_delay_s=round(play_start - u["end_wall"], 3),
                stt_delay_s=round(u["stt_done_wall"] - u["end_wall"], 3),
                mt_s=round(u["mt_done_wall"] - u["mt_start_wall"], 3),
                tts_rtf=round((done - t0) / audio_s, 4) if audio_s else None,
            )  # fmt: skip
            units_f.write(json.dumps(u, ensure_ascii=False) + "\n")
            units_f.flush()
            busy["tts"] -= 1

    threads = [threading.Thread(target=f, daemon=True) for f in (commit, translate, synth)]
    for t in threads:
        t.start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _reply(self, obj: dict) -> None:
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            alive = any(t.is_alive() for t in threads)
            self._reply({"ready": state["ready"], "done": not alive, "busy": dict(busy)})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/ingest/segment":
                seg_q.put(body)
                self._reply({"accepting": True})
            elif self.path == "/finish":
                state["finishing"] = True
                self._reply({"ok": True})
            else:
                self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", args.hub_port), Handler)
    state["ready"] = True
    server.serve_forever()
    return 0


# --------------------------------------------------------------------------- run role


class Sampler(threading.Thread):
    def __init__(self, path: Path):
        super().__init__(daemon=True)
        self.path, self.stop = path, threading.Event()
        self.procs: dict[str, int] = {}
        self.peak_vram = 0
        self.peak_rss: dict[str, int] = {}

    @staticmethod
    def vram() -> int:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True,
        )  # fmt: skip
        return int(r.stdout.strip().splitlines()[0])

    def run(self) -> None:
        import psutil

        with self.path.open("a", encoding="utf-8") as f:
            while not self.stop.is_set():
                rec: dict[str, Any] = {"wall": time.time(), "vram_mib": self.vram(), "rss_mib": {}}
                self.peak_vram = max(self.peak_vram, rec["vram_mib"])
                for name, pid in list(self.procs.items()):
                    try:
                        p = psutil.Process(pid)
                        rss = p.memory_info().rss + sum(
                            c.memory_info().rss for c in p.children(recursive=True)
                        )
                    except psutil.Error:
                        continue
                    rec["rss_mib"][name] = rss // 2**20
                    self.peak_rss[name] = max(self.peak_rss.get(name, 0), rss // 2**20)
                rec["cpu_pct"] = psutil.cpu_percent()
                f.write(json.dumps(rec) + "\n")
                f.flush()
                self.stop.wait(0.5)


def wait_for(check, what: str, timeout: float = 180.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if check():
                return
        except Exception:
            pass
        time.sleep(1.0)
    raise SystemExit(f"{what} did not come up in {timeout:.0f} s")


def cmd_run(args: argparse.Namespace) -> int:
    out = OUT_ROOT / args.name
    if out.exists():
        sys.exit(f"{out} exists; pick a new --name")
    for port in [SERVER_PORT, HUB_PORT] + ([LLAMA_PORT] if args.translate == "D" else []):
        if port_busy(port):
            sys.exit(f"port {port} is in use; stop that first")
    out.mkdir(parents=True)
    me = str(Path(__file__).resolve())
    settings = vars(args).copy()
    settings.pop("func", None)
    (out / "settings.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")

    idle = [Sampler.vram() for _ in range(5)]
    idle_vram = min(idle)
    sampler = Sampler(out / "samples.jsonl")
    sampler.start()
    procs: dict[str, subprocess.Popen] = {}
    logs = []

    def start(name: str, argv: list[str], cwd: Path = REPO) -> None:
        f = (out / f"{name}.log").open("w", encoding="utf-8")
        logs.append(f)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        procs[name] = subprocess.Popen(argv, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, env=env)
        sampler.procs[name] = procs[name].pid

    try:
        start(
            "server",
            [str(PROJECT_PY), "-m", "uvicorn", "--factory", "core.server.app:create_app",
             "--host", "127.0.0.1", "--port", str(SERVER_PORT)],
        )  # fmt: skip
        wait_for(lambda: port_busy(SERVER_PORT), "BibleLive server")
        if args.translate == "D":
            start(
                "llama",
                [str(LLAMA_SERVER), "-m", str(EUROLLM), "-ngl", "99", "-c", "2048", "-np", "1",
                 "--host", "127.0.0.1", "--port", str(LLAMA_PORT)],
            )  # fmt: skip
            wait_for(
                lambda: b"ok" in urllib.request.urlopen(
                    f"http://127.0.0.1:{LLAMA_PORT}/health", timeout=2
                ).read(),
                "llama-server",
            )  # fmt: skip
        if args.translate:
            start(
                "hub",
                [sys.executable, me, "hub", "--out", str(out), "--translate", args.translate,
                 "--voice", args.voice, "--tts-threads", str(args.tts_threads),
                 "--mt-threads", str(args.mt_threads), "--mt-device", args.mt_device,
                 "--hub-port", str(HUB_PORT)],
            )  # fmt: skip
            wait_for(lambda: http_json(f"http://127.0.0.1:{HUB_PORT}/status")["ready"], "hub", 300)
        pipe = [
            str(PROJECT_PY), me, "pipeline", "--out", str(out), "--audio", str(args.audio),
            "--start", str(args.start), "--dur", str(args.dur), "--stt", args.stt,
            "--device", args.device, "--cpu-threads", str(args.cpu_threads),
            "--canary-device", args.canary_device or "", "--canary-threads", str(args.canary_threads),
            "--hub-port", str(HUB_PORT),
        ] + (["--tap"] if args.translate else [])  # fmt: skip
        t_start = time.time()
        start("pipeline", pipe)
        print(f"running {args.dur + TAIL_S:.0f} s of audio (+ model load) -> {out}", flush=True)
        if procs["pipeline"].wait() != 0:
            raise SystemExit(
                f"pipeline exited with {procs['pipeline'].returncode}; see pipeline.log"
            )
        if args.translate:
            http_json(f"http://127.0.0.1:{HUB_PORT}/finish", {})
            wait_for(
                lambda: http_json(f"http://127.0.0.1:{HUB_PORT}/status")["done"], "hub drain", 600
            )
        wall_s = time.time() - t_start
    finally:
        sampler.stop.set()
        sampler.join(5)
        for name in ("pipeline", "hub", "llama", "server"):
            p = procs.get(name)
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(15)
                except subprocess.TimeoutExpired:
                    p.kill()
        for f in logs:
            f.close()

    summary = summarize(out, args, idle_vram, sampler, wall_s)
    (out / "run.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


def detections(timing: list[dict]) -> list[tuple]:
    out = []
    for u in timing:
        if u["type"] != "utterance":
            continue
        for r in u["references"]:
            out.append(
                (round(u["end_s"] or 0, 1), r.get("book"), r.get("chapter"),
                 r.get("verse_start"), r.get("verse_end"), r.get("confidence"))
            )  # fmt: skip
    return out


def summarize(out: Path, args, idle_vram: int, sampler: Sampler, wall_s: float) -> dict:
    timing = read_jsonl(out / "timing.jsonl")
    utts = [u for u in timing if u["type"] == "utterance" and u.get("queued_wall")]
    total = [u["done_wall"] - u["queued_wall"] for u in utts]
    wait = [max(0.0, u["done_wall"] - sum(u["stt_ms"]) / 1000 - u["queued_wall"]) for u in utts]
    stt_rtf = [
        sum(u["stt_ms"]) / 1000 / (u["end_s"] - u["start_s"])
        for u in utts
        if u.get("end_s") and u.get("start_s") is not None and u["end_s"] > u["start_s"]
    ]
    det = detections(timing)
    s: dict = {
        "name": args.name,
        "settings": {k: v for k, v in vars(args).items() if k != "func"},
        "wall_s": round(wall_s, 1),
        "video_mib": {
            "idle": idle_vram,
            "peak": sampler.peak_vram,
            "added": sampler.peak_vram - idle_vram,
        },
        "peak_rss_mib": sampler.peak_rss,
        "biblelive": {
            "utterances": len(utts),
            "final_s_from_end": stats(total),  # utterance closed -> references ready
            "queue_wait_s": stats(wait),  # time before the worker took it
            "stt_rtf": stats(stt_rtf),
            "detections": len(det),
        },
    }
    taps = [t for t in timing if t["type"] == "tap"]
    if taps:
        s["tap"] = {
            "segments": len(taps),
            "stt_delay_s": stats([t["stt_done_wall"] - t["end_wall"] for t in taps]),
            "stt_rtf": stats(
                [
                    (t["stt_done_wall"] - t["stt_start_wall"]) / t["audio_s"]
                    for t in taps
                    if t["audio_s"]
                ]
            ),
            "seg_len_s": stats([t["audio_s"] for t in taps]),
            "errors": sum(1 for t in timing if t["type"] == "tap_error"),
        }
    units = read_jsonl(out / "units.jsonl")
    if units:
        s["translate"] = {
            "sentences": len(units),
            "text_delay_s": stats([u["text_delay_s"] for u in units]),
            "audio_start_delay_s": stats([u["audio_start_delay_s"] for u in units]),
            "mt_s": stats([u["mt_s"] for u in units]),
            "tts_rtf": stats([u["tts_rtf"] for u in units if u["tts_rtf"] is not None]),
            "speech_out_s": round(sum(u["tts_audio_s"] for u in units), 1),
        }
    if args.baseline:
        base = read_jsonl(OUT_ROOT / args.baseline / "timing.jsonl")
        bdet = detections(base)
        same = sorted(map(tuple, det)) == sorted(map(tuple, bdet))
        s["vs_baseline"] = {
            "baseline": args.baseline,
            "detections_identical": same,
            "only_here": sorted(set(det) - set(bdet)),
            "only_baseline": sorted(set(bdet) - set(det)),
        }
    return s


# --------------------------------------------------------------------------- CLI


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--name", required=True)
    r.add_argument("--audio", default=str(DEFAULT_AUDIO))
    r.add_argument("--start", type=float, default=1900.0)
    r.add_argument("--dur", type=float, default=1200.0)
    r.add_argument("--stt", default="kmynas+canary", choices=["kmynas", "kmynas+canary"])
    r.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    r.add_argument("--cpu-threads", type=int, default=0)
    r.add_argument("--canary-device", default="", choices=["", "cpu", "cuda"])
    r.add_argument("--canary-threads", type=int, default=0)
    r.add_argument("--translate", default="", choices=["", "B", "D"])
    r.add_argument("--mt-device", default="cpu", choices=["cpu", "cuda"])
    r.add_argument("--mt-threads", type=int, default=3)
    r.add_argument("--voice", default="ru_aigul")
    r.add_argument("--tts-threads", type=int, default=4)
    r.add_argument("--baseline", default="", help="run name without Translate to compare with")

    p = sub.add_parser("pipeline")
    p.add_argument("--out", required=True)
    p.add_argument("--audio", required=True)
    p.add_argument("--start", type=float, required=True)
    p.add_argument("--dur", type=float, required=True)
    p.add_argument("--stt", required=True)
    p.add_argument("--device", required=True)
    p.add_argument("--cpu-threads", type=int, default=0)
    p.add_argument("--canary-device", default="")
    p.add_argument("--canary-threads", type=int, default=0)
    p.add_argument("--tap", action="store_true")
    p.add_argument("--tap-eos", type=float, default=0.6)
    p.add_argument("--tap-soft", type=float, default=4.0)
    p.add_argument("--tap-hard", type=float, default=7.0)
    p.add_argument("--hub-port", type=int, default=HUB_PORT)

    h = sub.add_parser("hub")
    h.add_argument("--out", required=True)
    h.add_argument("--translate", required=True, choices=["B", "D"])
    h.add_argument("--voice", default="ru_aigul")
    h.add_argument("--tts-threads", type=int, default=4)
    h.add_argument("--mt-threads", type=int, default=3)
    h.add_argument("--mt-device", default="cpu")
    h.add_argument("--hub-port", type=int, default=HUB_PORT)

    args = ap.parse_args()
    return {"run": cmd_run, "pipeline": cmd_pipeline, "hub": cmd_hub}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
