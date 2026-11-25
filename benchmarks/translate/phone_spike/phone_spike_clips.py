"""Phase 0, P0.5: encode the Russian Silero clips for the phone audio spike.

Runs in the project venv. Takes the P0.4 Silero clips (both shipped voices,
alternating) and encodes each three ways with the bundled ffmpeg:

  mp3   MP3, 64 kbit/s mono 44.1 kHz              (Web Audio and <audio> players)
  aac   AAC in ADTS, 64 kbit/s mono 44.1 kHz      (Web Audio and <audio> players)
  m4a   AAC in fragmented MP4, same settings      (MediaSource player)

    python benchmarks/translate/phone_spike/phone_spike_clips.py

Output: results/translate/phone_spike/clips/{mp3,aac,m4a}/NN.* and manifest.json
with {"name", "text", "voice", "duration_s"} per clip.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
# The clips live in the main checkout's results/ even when this runs from a worktree.
RESULTS = Path(os.environ.get("BL_RESULTS", REPO_ROOT / "results"))
TTS_DIR = RESULTS / "translate" / "tts"
OUT_DIR = RESULTS / "translate" / "phone_spike" / "clips"
VOICES = ("silero-ru_aigul-cpu4", "silero-ru_miyau-cpu4")
BUNDLED_FFMPEG = REPO_ROOT / "build" / "BibleLive" / "app" / "ffmpeg" / "ffmpeg.exe"

ENCODINGS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3"],
    "aac": ["-c:a", "aac", "-b:a", "64k", "-f", "adts"],
    "m4a": [
        "-c:a", "aac", "-b:a", "64k", "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
    ],
}  # fmt: skip


def find_ffmpeg(explicit: str | None) -> str:
    for cand in (explicit, os.environ.get("BL_FFMPEG"), str(BUNDLED_FFMPEG)):
        if cand and Path(cand).exists():
            return cand
    found = shutil.which("ffmpeg")
    if not found:
        sys.exit("ffmpeg not found: pass --ffmpeg or set BL_FFMPEG")
    return found


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def interleave(lists: list[list[dict]]) -> list[dict]:
    """a1 b1 a2 b2 ... (longer list's tail appended)."""
    out: list[dict] = []
    for i in range(max((len(x) for x in lists), default=0)):
        out.extend(x[i] for x in lists if i < len(x))
    return out


def load_voice(voice_dir: Path) -> list[dict]:
    results = voice_dir / "results.jsonl"
    texts: dict[str, str] = {}
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                key = Path(
                    str(r.get("file") or r.get("wav") or f"{int(r.get('no', 0)):02d}.wav")
                ).name
                texts[key] = str(r.get("text", ""))
    return [
        {"wav": p, "text": texts.get(p.name, ""), "voice": voice_dir.name}
        for p in sorted(voice_dir.glob("*.wav"))
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ffmpeg")
    args = ap.parse_args()
    ffmpeg = find_ffmpeg(args.ffmpeg)
    print(f"ffmpeg: {ffmpeg}")

    clips = interleave([load_voice(TTS_DIR / v) for v in VOICES])
    if not clips:
        sys.exit(f"no clips under {TTS_DIR}")
    for kind in ENCODINGS:
        (OUT_DIR / kind).mkdir(parents=True, exist_ok=True)
    # "00": one second of silence, which the MediaSource player appends between clips so the
    # element never runs dry (not part of the broadcast schedule).
    for kind, opts in ENCODINGS.items():
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", "anullsrc=r=44100:cl=mono", "-t", "1.0", "-ac", "1", "-ar", "44100",
               *opts, str(OUT_DIR / kind / f"00.{kind}")]  # fmt: skip
        subprocess.run(cmd, check=True)
    manifest = []
    for i, clip in enumerate(clips, 1):
        name = f"{i:02d}"
        for kind, opts in ENCODINGS.items():
            dst = OUT_DIR / kind / f"{name}.{kind}"
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(clip["wav"]),
                   "-ac", "1", "-ar", "44100", *opts, str(dst)]  # fmt: skip
            subprocess.run(cmd, check=True)
        manifest.append({
            "name": name, "text": clip["text"], "voice": clip["voice"],
            "duration_s": round(wav_duration(clip["wav"]), 3),
        })  # fmt: skip
    (OUT_DIR / "manifest.json").write_text(
        json.dumps({"ffmpeg": ffmpeg, "clips": manifest}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    total = sum(c["duration_s"] for c in manifest)
    print(f"{len(manifest)} clips, {total:.0f} s of audio -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
