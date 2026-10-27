#!/usr/bin/env python3
"""Spike: speech engines on macOS arm64 through the pinned wheels.

Pre-pin check from the macOS installer plan, not the acceptance test. It
proves, in order:

1. the native provider descriptor lists its backend kinds (expected: cpu,
   metal; Metal exists in the bundle but v1 runs CPU only on purpose),
2. an explicit ``backend="cpu"`` load reports ``model.backend == "cpu"``,
3. inference runs on 1 s of silence (the load/run path),
4. when a fixture WAV is given, the recognizer's text **and** the BibleLive
   parser must yield the expected reference: a capability smoke, not an
   accuracy measurement. A single synthesized utterance can pronounce a
   reference unlike a real speaker, so production recognition accuracy stays
   with the detection benchmark. A missing fixture prints ``BLOCKED`` instead
   of passing,
5. English Whisper ``small`` int8 smoke via faster-whisper (kept separate),
6. timings: the cold load and every run are printed as ``TIMING {json}`` lines
   so the acceptance-test threshold is set from several trials, never one run.

Exit code 0 when everything that ran passed, 1 on any failure. BLOCKED results
do not fail the process; the workflow's verdict section decides what they mean
for pinning.
"""

from __future__ import annotations

import argparse
import json
import time
import wave

import numpy as np

SAMPLE_RATE = 16000
SILENCE_SECONDS = 1.0
DEFAULT_THREADS = 6


def read_wav_16k_mono(path: str) -> np.ndarray:
    """Read a PCM16 WAV that is already 16 kHz mono, as float32 samples."""
    with wave.open(path, "rb") as wav:
        if wav.getframerate() != SAMPLE_RATE or wav.getnchannels() != 1:
            raise SystemExit(
                f"{path}: expected {SAMPLE_RATE} Hz mono, got "
                f"{wav.getframerate()} Hz x{wav.getnchannels()}"
            )
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def parse_references(text: str, language: str) -> list[str]:
    """Run the BibleLive parser over one transcript (the same entry the tests use)."""
    from core.verse_parser import ReferenceParser

    return [str(ref) for ref in ReferenceParser(language=language).parse(text)]


def timing(engine: str, kind: str, seconds_value: float, audio_s: float, trial: int = 0) -> None:
    print(
        "TIMING "
        + json.dumps(
            {
                "engine": engine,
                "kind": kind,
                "trial": trial,
                "seconds": round(seconds_value, 3),
                "audio_s": round(audio_s, 2),
            }
        ),
        flush=True,
    )


def check_gguf_backends() -> bool:
    import transcribe_cpp
    import transcribe_cpp_native

    descriptor = transcribe_cpp_native.descriptor()
    backends = [str(name) for name in descriptor.get("backends", [])]
    print(f"native provider: {descriptor.get('name')} (version {descriptor.get('version')})")
    print(f"native backends: {backends}")
    print(f"selected provider: {transcribe_cpp.selected_provider()}")
    ok = "cpu" in backends
    print(
        f"GGUF BACKENDS: {'PASS' if ok else 'FAIL'}"
        f" (cpu {'present' if ok else 'missing'};"
        f" metal {'present' if 'metal' in backends else 'absent'})"
    )
    return ok


def run_gguf(
    model_path: str, audio: np.ndarray, threads: int, trials: int, label: str
) -> tuple[bool, str]:
    """Load with an explicit CPU backend and transcribe ``audio`` ``trials`` times."""
    import transcribe_cpp

    audio_s = len(audio) / SAMPLE_RATE
    start = time.perf_counter()
    with transcribe_cpp.Model(model_path, backend="cpu") as model:
        timing(label, "cold_load", time.perf_counter() - start, audio_s)
        # The native library reports the resolved backend in its own casing
        # ("CPU" on macOS arm64), so compare case-insensitively. The log line from
        # the library ("parakeet: using cpu backend (strict)") is the second witness.
        if model.backend.upper() != "CPU":
            print(f"{label}: FAIL - requested backend='cpu', got {model.backend!r}")
            return False, ""
        text = ""
        with model.session(n_threads=threads) as session:
            for trial in range(1, trials + 1):
                start = time.perf_counter()
                text = session.run(memoryview(audio), language="lt").text or ""
                timing(label, "run", time.perf_counter() - start, audio_s, trial)
    print(f"{label}: backend={model.backend!r} text={text!r}")
    return True, text


def run_whisper(wav_path: str, model_name: str, trials: int) -> tuple[bool, str]:
    """English smoke: faster-whisper ``small`` int8 on the processor."""
    from faster_whisper import WhisperModel

    audio = read_wav_16k_mono(wav_path)
    audio_s = len(audio) / SAMPLE_RATE
    start = time.perf_counter()
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    timing(f"whisper/{model_name}", "cold_load", time.perf_counter() - start, audio_s)
    text = ""
    for trial in range(1, trials + 1):
        start = time.perf_counter()
        segments, _info = model.transcribe(audio, language="en")
        text = "".join(segment.text for segment in segments).strip()
        timing(f"whisper/{model_name}", "run", time.perf_counter() - start, audio_s, trial)
    print(f"whisper/{model_name}: text={text!r}")
    return True, text


def main() -> int:
    parser = argparse.ArgumentParser(description="macOS speech spike")
    parser.add_argument("--gguf-repo", default="Ezomas/kmynas-parakeet-lt-v4-gguf")
    parser.add_argument("--gguf-file", default="kmynas-v4-Q8_0.gguf")
    parser.add_argument("--lt-wav", default="", help="Lithuanian fixture (empty = BLOCKED)")
    parser.add_argument("--lt-expect", default="John 3:16")
    parser.add_argument("--en-wav", default="", help="English fixture (empty = BLOCKED)")
    parser.add_argument("--en-expect", default="John 3:16")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    args = parser.parse_args()

    print(
        "NOTE: the fixtures are a capability smoke; recognition accuracy is the "
        "detection benchmark's job, not this script's."
    )
    failures: list[str] = []
    if not check_gguf_backends():
        failures.append("native backends")

    from huggingface_hub import hf_hub_download

    print(f"downloading {args.gguf_repo}/{args.gguf_file} ...")
    model_path = hf_hub_download(repo_id=args.gguf_repo, filename=args.gguf_file)
    print(f"model: {model_path}")

    silence = np.zeros(int(SILENCE_SECONDS * SAMPLE_RATE), dtype=np.float32)
    ok, _ = run_gguf(model_path, silence, args.threads, 1, "kmynas/silence")
    if not ok:
        failures.append("explicit cpu backend (silence run)")

    if args.lt_wav:
        audio = read_wav_16k_mono(args.lt_wav)
        ok, text = run_gguf(model_path, audio, args.threads, args.trials, "kmynas")
        refs = parse_references(text, "lt")
        found = args.lt_expect in refs
        print(
            f"LT SMOKE: {'PASS' if ok and found else 'FAIL'}"
            f" - text={text!r} refs={refs} expected={args.lt_expect!r}"
        )
        if not (ok and found):
            failures.append("lithuanian smoke")
    else:
        print("LT SMOKE: BLOCKED - no Lithuanian fixture on this run")

    if args.en_wav:
        ok, text = run_whisper(args.en_wav, args.whisper_model, args.trials)
        refs = parse_references(text, "en")
        found = args.en_expect in refs
        print(
            f"EN SMOKE: {'PASS' if ok and found else 'FAIL'}"
            f" - text={text!r} refs={refs} expected={args.en_expect!r}"
        )
        if not (ok and found):
            failures.append("english whisper smoke")
    else:
        print("EN SMOKE: BLOCKED - no English fixture on this run")

    verdict = "PASS" if not failures else "FAIL (" + ", ".join(failures) + ")"
    print(f"SPEECH SPIKE: {verdict}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
