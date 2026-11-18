"""Acceptance test for the packaged Mac app: speech speed and end-to-end latency (Phase 2).

Runs with the app's own Python, so it measures the shipped interpreter, wheels and model:

    /Applications/BibleLive.app/Contents/Resources/python/bin/python3 -I \\
        scripts/macos/acceptance.py --audio SERMON.wav --t-range 2280:3260 \\
        --bench-result rt-10-08.json --out acceptance.json --check

What it measures, on the Kmynas model the app runs for Lithuanian (CPU, the launcher's thread
count unless --threads is given):

* cold: ``--cold`` fresh processes, each loading the model and transcribing one utterance
  (process start to text, as the first utterance of a service sees it). The first one after
  an install is the slowest (macOS checks the new libraries and reads the model from disk),
  so run the test on a freshly installed app at least once;
* warm: in one process, every utterance ``--warm`` times; the real-time factor (RTF) is the
  transcription time over the utterance's length;
* utterances are ``--utterance-s`` long (default 12 s: the pipeline's hard cap, the slowest
  case a live service meets), cut from ``--t-range`` of ``--audio`` with the app's ffmpeg;
* end to end: ``--bench-result`` files from ``benchmarks/run_benchmark.py --mode rt`` (the full
  pipeline fed at the speaker's pace); their latency is detection time minus the middle of the
  spoken reference, as in the benchmark.

The model file is checked against its pinned SHA256 first. ``--check`` fails (exit 1) when a
number is over its threshold below; the thresholds and the machine that set them are recorded
in docs/macos-arm64.md ("Acceptance test").
"""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

SAMPLE_RATE = 16000

# Thresholds (docs/macos-arm64.md, "Acceptance test"): set from several cold and warm trials on
# the recorded machine, with headroom; a slower Mac failing them is a finding, not a reason to
# raise them.
THRESHOLDS = {
    # Slowest warm 12 s utterance, as a fraction of its length: 0.072 measured at 3 threads on
    # an M1; 0.25 still leaves the next utterance waiting at most 3 s.
    "warm_rtf_max": 0.25,
    # Process start -> first utterance's text, slowest trial. The first load after an install
    # (or after the files were last read long ago) took 8 s on the M1, later ones 0.4 s.
    "cold_first_text_s_max": 20.0,
    "latency_s_max": 15.0,  # slowest detected reference, end to end (rt benchmark)
    "latency_s_mean_max": 8.0,  # mean over the detected references (rt benchmark)
}


def _app_ffmpeg() -> str:
    from core.config import PROJECT_ROOT

    bundled = Path(PROJECT_ROOT) / "ffmpeg" / "ffmpeg"
    return str(bundled) if bundled.is_file() else "ffmpeg"


def _utterances(audio: str, t_range: str, utterance_s: float, count: int) -> list[bytes]:
    start, end = (float(x) for x in t_range.split(":"))
    pcm = subprocess.run(
        [
            _app_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(start),
            "-t",
            str(end - start),
            "-i",
            audio,
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    step = int(utterance_s * SAMPLE_RATE) * 2
    chunks = [pcm[i : i + step] for i in range(0, len(pcm) - step + 1, step)]
    if len(chunks) < count:
        raise SystemExit(
            f"--t-range holds {len(chunks)} utterances of {utterance_s} s, need {count}"
        )
    # Spread over the range, not just its first minutes.
    stride = len(chunks) / count
    return [chunks[int(i * stride)] for i in range(count)]


def _model_path(check_hash: bool) -> str:
    from launcher.settings import models_dir
    from launcher.speech import GGUF_MODELS, cached_gguf_path, file_sha256

    path = cached_gguf_path("kmynas", models_dir(), pinned_only=True)
    if path is None:
        raise SystemExit("Kmynas is not downloaded at its pinned commit: run the app's setup first")
    if check_hash and file_sha256(path) != GGUF_MODELS["kmynas"].sha256:
        raise SystemExit(f"{path} does not match its pinned SHA256")
    return path


def _worker(args: argparse.Namespace) -> int:
    """One measuring process: prints a JSON line."""
    import numpy as np

    from audio.stt_gguf import GgufSpeechToText

    utterances = _utterances(args.audio, args.t_range, args.utterance_s, args.utterances)
    samples = [np.frombuffer(u, dtype=np.int16).astype(np.float32) / 32768.0 for u in utterances]
    load_start = time.perf_counter()
    stt = GgufSpeechToText("kmynas", args.model_path, device="cpu", cpu_threads=args.threads)
    load_s = time.perf_counter() - load_start
    if args.worker == "cold":
        t0 = time.perf_counter()
        text = stt.transcribe(samples[0], "lt").text
        print(json.dumps({"load_s": load_s, "first_s": time.perf_counter() - t0, "text": text}))
        return 0
    stt.transcribe(samples[0], "lt")  # warm-up, not counted
    runs = []
    for trial in range(args.warm):
        for index, audio in enumerate(samples):
            t0 = time.perf_counter()
            stt.transcribe(audio, "lt")
            runs.append({"trial": trial, "utterance": index, "s": time.perf_counter() - t0})
    print(json.dumps({"load_s": load_s, "runs": runs}))
    return 0


def _run_worker(kind: str, args: argparse.Namespace, model_path: str) -> dict:
    cmd = [
        sys.executable,
        "-I",
        os.path.abspath(__file__),
        "--worker",
        kind,
        "--model-path",
        model_path,
        "--threads",
        str(args.threads),
        "--audio",
        args.audio,
        "--t-range",
        args.t_range,
        "--utterance-s",
        str(args.utterance_s),
        "--utterances",
        str(1 if kind == "cold" else args.utterances),
        "--warm",
        str(args.warm),
    ]
    start = time.perf_counter()
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    result = json.loads(out.strip().splitlines()[-1])
    result["process_s"] = time.perf_counter() - start
    return result


def _machine() -> dict:
    def sysctl(name: str) -> str:
        try:
            return subprocess.run(
                ["sysctl", "-n", name], capture_output=True, text=True
            ).stdout.strip()
        except OSError:
            return ""

    return {
        "chip": sysctl("machdep.cpu.brand_string"),
        "cores": sysctl("hw.physicalcpu"),
        "performance_cores": sysctl("hw.perflevel0.physicalcpu"),
        "memory_gb": round(int(sysctl("hw.memsize") or 0) / 2**30),
        "macos": platform.mac_ver()[0],
        "arch": platform.machine(),
    }


def _bench(paths: list[str]) -> dict:
    latencies: list[float] = []
    records = []
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("mode") != "rt":
            raise SystemExit(f"{path} is a {data.get('mode')} run; latency needs --mode rt")
        for rec in data["recordings"]:
            latencies += [m["latency_s"] for m in rec.get("matches", [])]
            records.append(
                {
                    k: rec.get(k)
                    for k in (
                        "record_id",
                        "audio_duration_s",
                        "rtf",
                        "gold_count",
                        "tp",
                        "fp",
                        "fn",
                        "latency_s",
                    )
                }
            )
    return {
        "records": records,
        "latency_s_mean": round(statistics.mean(latencies), 2) if latencies else None,
        "latency_s_max": round(max(latencies), 2) if latencies else None,
        "latency_s": sorted(round(x, 2) for x in latencies),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Acceptance test for the packaged Mac app.")
    ap.add_argument("--audio", required=True, help="a Lithuanian sermon recording")
    ap.add_argument("--t-range", required=True, help="START:END seconds of speech to cut from")
    ap.add_argument("--utterance-s", type=float, default=12.0)
    ap.add_argument("--utterances", type=int, default=10)
    ap.add_argument("--cold", type=int, default=3, help="cold trials (fresh processes)")
    ap.add_argument("--warm", type=int, default=3, help="warm trials over every utterance")
    ap.add_argument("--threads", type=int, default=0, help="default: the launcher's choice")
    ap.add_argument(
        "--bench-result", action="append", default=[], help="run_benchmark --mode rt output"
    )
    ap.add_argument("--out", help="write the report here (JSON)")
    ap.add_argument("--check", action="store_true", help="exit 1 if a threshold is missed")
    ap.add_argument("--worker", choices=["cold", "warm"], help=argparse.SUPPRESS)
    ap.add_argument("--model-path", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.worker:
        return _worker(args)

    from launcher.speech import speech_threads

    from core.version import version_string

    args.threads = args.threads or speech_threads()
    model_path = _model_path(check_hash=True)
    cold = [_run_worker("cold", args, model_path) for _ in range(args.cold)]
    warm = _run_worker("warm", args, model_path)
    rtfs = [run["s"] / args.utterance_s for run in warm["runs"]]
    report = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "app": version_string(),
        "python": sys.executable,
        "machine": _machine(),
        "model": {"name": "kmynas", "file": os.path.basename(model_path), "sha256_checked": True},
        "threads": args.threads,
        "utterance_s": args.utterance_s,
        "utterances": args.utterances,
        "cold": [
            {
                "process_to_text_s": round(c["process_s"], 2),
                "load_s": round(c["load_s"], 2),
                "first_utterance_s": round(c["first_s"], 2),
            }
            for c in cold
        ],
        "warm": {
            "trials": args.warm,
            "load_s": round(warm["load_s"], 2),
            "rtf_median": round(statistics.median(rtfs), 3),
            "rtf_max": round(max(rtfs), 3),
            "rtf_per_trial_median": [
                round(
                    statistics.median(
                        r["s"] / args.utterance_s for r in warm["runs"] if r["trial"] == t
                    ),
                    3,
                )
                for t in range(args.warm)
            ],
        },
        "end_to_end": _bench(args.bench_result) if args.bench_result else None,
        "thresholds": THRESHOLDS,
    }
    failures = []
    if report["warm"]["rtf_max"] > THRESHOLDS["warm_rtf_max"]:
        failures.append(f"warm RTF {report['warm']['rtf_max']} > {THRESHOLDS['warm_rtf_max']}")
    slowest_cold = max(c["process_to_text_s"] for c in report["cold"])
    if slowest_cold > THRESHOLDS["cold_first_text_s_max"]:
        failures.append(
            f"cold first text {slowest_cold} s > {THRESHOLDS['cold_first_text_s_max']} s"
        )
    e2e = report["end_to_end"]
    if e2e is None:
        failures.append("no --bench-result: end-to-end latency not measured")
    else:
        if e2e["latency_s_max"] is None:
            failures.append("the rt benchmark detected no reference")
        else:
            if e2e["latency_s_max"] > THRESHOLDS["latency_s_max"]:
                failures.append(
                    f"latency max {e2e['latency_s_max']} s > {THRESHOLDS['latency_s_max']} s"
                )
            if e2e["latency_s_mean"] > THRESHOLDS["latency_s_mean_max"]:
                failures.append(
                    f"latency mean {e2e['latency_s_mean']} s > {THRESHOLDS['latency_s_mean_max']} s"
                )
    report["failures"] = failures
    report["passed"] = not failures
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    print("ACCEPTANCE " + ("PASSED" if not failures else "FAILED: " + "; ".join(failures)))
    return 1 if (args.check and failures) else 0


if __name__ == "__main__":
    sys.exit(main())
