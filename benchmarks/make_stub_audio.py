"""Generate a synthetic smoke-test recording for the benchmark runner.

Builds a WAV with silence/tone segments shaped like a spoken Bible reference
(long pause, short tone bursts mimicking speech cadence, long pause). This
verifies the runner end-to-end (frame feed, VAD, STT call, parser, metrics)
without any copyrighted content — it is NOT a quality benchmark, and the
transcription is expected to be garbage: the failure report should show
reference-misses caused by STT, which is exactly what a smoke test should
demonstrate. For a real quality baseline use a real recording.

Usage:
    python benchmarks/make_stub_audio.py --out bench_audio/smoke.wav
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

# Deterministic tone pattern: 1.5s silence, 6 x (0.5s tone / 0.3s gap), 1.5s silence.
TONE_SEGMENTS = [(15.0, "silence")]
for i in range(6):
    TONE_SEGMENTS.append((0.5, "tone"))
    if i < 5:
        TONE_SEGMENTS.append((0.3, "silence"))
TONE_SEGMENTS.append((15.0, "silence"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="bench_audio/smoke.wav")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found on PATH; cannot synthesize stub audio", file=sys.stderr)
        return 1

    # Build a single filtergraph of concatenated tone/silence segments.
    # Tone: 440 Hz sine, low volume so it resembles measured speech cadence.
    parts = []
    for dur, kind in TONE_SEGMENTS:
        if kind == "tone":
            src = f"aevalsrc=0.15*sin(880*t):s=16000:d={dur}"
        else:
            src = f"aevalsrc=0:s=16000:d={dur}"
        parts.extend(["-f", "lavfi", "-i", src])
    n = len(TONE_SEGMENTS)
    concat = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    ffmpeg_cmd = (
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        + parts
        + [
            "-filter_complex",
            concat,
            "-map",
            "[out]",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out),
        ]
    )
    try:
        subprocess.run(ffmpeg_cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg failed: {e}", file=sys.stderr)
        return 1

    # Manifest-style hash so the stub can be added to a local manifest if wanted.
    h = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"stub audio written: {out}  sha256={h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
