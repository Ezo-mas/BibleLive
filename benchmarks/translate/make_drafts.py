"""Phase 0, P0.1: cut the six transcript windows and write Kmynas draft transcripts.

For each window in ``WINDOWS`` (from docs/translate/phase0/TRANSCRIPT_CORRECTION.md):

1. cut ``bench_audio/<file>`` [start, end) to ``results/translate/clips/<id>.wav``
   (16 kHz mono s16, ffmpeg; the source is only read);
2. run the clip through BibleLive's live segmenter (default settings) and
   Kmynas, and write ``results/translate/drafts/<id>.txt`` in the correction
   format, with times in seconds from the start of the source file.

Usage (repo root):
    .venv/Scripts/python.exe benchmarks/translate/make_drafts.py \
        --kmynas-model C:/path/kmynas-v4-Q8_0.gguf [--device cuda|cpu] [--only 1011-cite]

Existing drafts are never overwritten (they may hold the owner's corrections);
pass ``--force`` to regenerate.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from audio.config import AudioPipelineConfig  # noqa: E402
from audio.segmenter import FRAME_DURATION_MS, Segmenter  # noqa: E402
from audio.stt_gguf import create_gguf_speech_to_text  # noqa: E402
from audio.vad import create_vad  # noqa: E402

# (window id, source file, start s, end s)
WINDOWS: list[tuple[str, str, float, float]] = [
    ("1011-cite", "benchmark_audio_10-11-norm.wav", 5150, 5450),
    # 1011-plain and 1004-plain: the doc's windows (3300-3600, 3400-3700) were worship
    # music; shifted by whole minutes into the sermons (no gold citations there).
    ("1011-plain", "benchmark_audio_10-11-norm.wav", 5640, 5940),
    ("1004-cite", "benchmark_audio_10-04-norm.wav", 4360, 4660),
    ("1004-plain", "benchmark_audio_10-04-norm.wav", 5730, 6030),
    ("0924-cite", "benchmark_audio_09-24-norm.wav", 3470, 3770),
    ("0924-plain", "benchmark_audio_09-24-norm.wav", 3950, 4250),
]

OUT_ROOT = REPO_ROOT / "results" / "translate"
SAMPLE_RATE = 16000


def cut_clip(src: Path, dst: Path, start: float, end: float) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(start), "-t", str(end - start), "-i", str(src),
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", str(dst),
    ]  # fmt: skip
    subprocess.run(cmd, check=True)


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{path}: expected 16 kHz mono s16")
        return w.readframes(w.getnframes())


def transcribe_clip(pcm: bytes, offset: float, stt, config: AudioPipelineConfig) -> list[str]:
    """Segment like the live pipeline; return correction-format lines."""
    vad = create_vad(config.vad, aggressiveness=config.vad_aggressiveness)
    segmenter = Segmenter.from_config(vad.is_speech, config)
    frame_s = FRAME_DURATION_MS / 1000.0
    frame_bytes = int(SAMPLE_RATE * frame_s) * 2
    n_frames = len(pcm) // frame_bytes
    lines: list[str] = []
    speech_start = 0.0

    def emit(end_t: float, frames: list[bytes]) -> None:
        result = stt.transcribe_utterance(b"".join(frames), "lt")
        text = (result.text or "").strip()
        if text:
            lines.append(f"[{offset + speech_start:.1f}-{offset + end_t:.1f}] {text}")

    for i in range(n_frames):
        t = (i + 1) * frame_s
        event = segmenter.push(pcm[i * frame_bytes : (i + 1) * frame_bytes])
        if event is None:
            continue
        if event.kind == "start":
            speech_start = max(0.0, t - event.onset_frames * frame_s)
        elif event.kind == "end":
            emit(t - event.trailing_silent_frames * frame_s, event.frames)
    final = segmenter.flush()
    if final is not None and final.kind == "end":
        emit(n_frames * frame_s, final.frames)
    return lines


def _no_whisper():
    raise RuntimeError("Whisper is not used for Lithuanian drafts")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kmynas-model", required=True)
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--cpu-threads", type=int, default=0)
    ap.add_argument("--audio-dir", default=str(REPO_ROOT / "bench_audio"))
    ap.add_argument("--only", nargs="*", help="window ids to process")
    ap.add_argument("--force", action="store_true", help="overwrite existing drafts")
    ap.add_argument(
        "--out-subdir",
        default="drafts",
        help="folder under results/translate/ ('recognized' keeps the raw output the owner never edits)",
    )
    args = ap.parse_args()

    config = AudioPipelineConfig(
        stt_engine="kmynas", kmynas_model=args.kmynas_model, whisper_device=args.device,
        cpu_threads=args.cpu_threads,
    )  # fmt: skip
    stt = create_gguf_speech_to_text(
        "kmynas", {"kmynas": args.kmynas_model, "canary": None},
        device=args.device, cpu_threads=args.cpu_threads, language="lt",
        whisper_factory=_no_whisper,
    )  # fmt: skip

    for wid, fname, start, end in WINDOWS:
        if args.only and wid not in args.only:
            continue
        clip = OUT_ROOT / "clips" / f"{wid}.wav"
        draft = OUT_ROOT / args.out_subdir / f"{wid}.txt"
        if draft.exists() and not args.force:
            print(f"{wid}: draft exists, skipped (use --force)")
            continue
        cut_clip(Path(args.audio_dir) / fname, clip, start, end)
        t0 = time.time()
        lines = transcribe_clip(read_pcm(clip), start, stt, config)
        header = [
            f"# window: {wid}",
            f"# file: {fname}",
            f"# range_s: {start:g}-{end:g}",
            f"# draft: kmynas {Path(args.kmynas_model).name} ({args.device}), default segmentation",
            "# corrected_by: <name>   date: <YYYY-MM-DD>",
        ]
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
        print(f"{wid}: {len(lines)} lines in {time.time() - t0:.1f}s -> {draft}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
