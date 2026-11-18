"""Phase 0, P0.2 on FLEURS lt_lt test (read speech, one sentence per clip).

Project venv. Each clip goes through the live VAD and ``Segmenter`` with one of the
P0.2 settings (stt_bench.SETTINGS), closed by a flush at the end of the clip; the
segments' texts are joined and scored against the FLEURS transcription. Reported:
WER over all clips and over the clips whose reference has no digits (FLEURS writes
numbers as digits, Kmynas and Canary as words), RTF, video memory.

    # once: unpack the test audio downloaded to ~/.venvs/models/fleurs-lt
    python benchmarks/translate/stt_fleurs.py unpack
    python benchmarks/translate/stt_fleurs.py run --system kmynas-gpu --name p02
    python benchmarks/translate/stt_fleurs.py report --name p02

Data: google/fleurs at revision 70bb2e84b976b7e960aa89f1c648e09c59f894dd (CC-BY-4.0),
hashes in results/translate/stt/fleurs-manifest.json.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import tarfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stt_bench import (  # noqa: E402
    FRAME_S,
    OUT,
    SETTINGS,
    SYSTEMS,
    VramPeak,
    edit_distance,
    load_engine,
    norm,
    peak_working_set_mib,
    segmenter,
    vram_mib,
)

DATA = Path.home() / ".venvs" / "models" / "fleurs-lt"
REVISION = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"
TSV = DATA / "data" / "lt_lt" / "test.tsv"
TAR = DATA / "data" / "lt_lt" / "audio" / "test.tar.gz"
AUDIO = DATA / "test-audio"  # as unpacked: 32-bit float WAV
# What the runs read: converted once to the pipeline's format (16 kHz mono s16) with
#   ffmpeg -i test-audio/X.wav -ac 1 -ar 16000 -c:a pcm_s16le test-audio-s16/X.wav
AUDIO_S16 = DATA / "test-audio-s16"


def items() -> list[dict]:
    """One row per clip (FLEURS lists some clips twice: several readings of a sentence
    are separate files, so rows are unique by file name)."""
    rows = []
    with TSV.open(encoding="utf-8", newline="") as f:
        for r in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            rows.append({"id": r[0], "file": r[1], "text": r[2]})
    return rows


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cmd_unpack(_args: argparse.Namespace) -> int:
    AUDIO.mkdir(parents=True, exist_ok=True)
    with tarfile.open(TAR) as t:
        members = [m for m in t.getmembers() if m.isfile() and m.name.endswith(".wav")]
        for m in members:
            m.name = Path(m.name).name  # flat, no paths from the archive
            t.extract(m, AUDIO, filter="data")
    manifest = {
        "dataset": "google/fleurs", "config": "lt_lt", "split": "test", "revision": REVISION,
        "license": "CC-BY-4.0", "clips": len(members), "rows": len(items()),
        "sha256": {"test.tsv": sha256(TSV), "test.tar.gz": sha256(TAR)},
    }  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fleurs-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps(manifest))
    return 0


def frames_of(path: Path) -> list[bytes]:
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        data = w.readframes(w.getnframes())
    n = int(16000 * FRAME_S) * 2
    return [data[i : i + n] for i in range(0, len(data) - n + 1, n)]


def cmd_run(args: argparse.Namespace) -> int:
    out = OUT / args.name / "fleurs" / args.system
    if out.exists():
        sys.exit(f"{out} exists")
    out.mkdir(parents=True)
    clips = {r["file"]: r for r in items()}
    before = vram_mib()
    peak = VramPeak()
    peak.start()
    stt = load_engine(args.system, args.threads)
    stt(b"\0\0" * 16000)
    for setting in args.settings:
        with (out / f"{setting}.jsonl").open("w", encoding="utf-8") as f:
            for name, row in clips.items():
                seg = segmenter(setting)
                evs = [ev for ev in map(seg.push, frames_of(AUDIO_S16 / name)) if ev is not None]
                last = seg.flush()
                if last is not None:
                    evs.append(last)
                texts, ms, audio = [], 0.0, 0.0
                for ev in evs:
                    if ev.kind != "end":
                        continue
                    t = time.perf_counter()
                    tr = stt(b"".join(ev.frames))
                    ms += (time.perf_counter() - t) * 1000
                    audio += len(ev.frames) * FRAME_S
                    texts.append(tr.text or "")
                rec = {"file": name, "id": row["id"], "segments": len(texts), "ms": round(ms, 1),
                       "audio_s": round(audio, 2), "text": " ".join(texts)}  # fmt: skip
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"fleurs {args.system} {setting} done", flush=True)
    peak.stop.set()
    peak.join()
    meta = {"system": args.system, "threads": args.threads, "settings": args.settings,
            "vram_added_mib": peak.peak - before, "peak_working_set_mib": peak_working_set_mib()}  # fmt: skip
    (out / "run.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return 0


def words(text: str) -> list[str]:
    return [n for n in (norm(t) for t in text.split()) if n]


def cmd_report(args: argparse.Namespace) -> int:
    ref = {r["file"]: r["text"] for r in items()}
    rows = []
    for d in sorted((OUT / args.name / "fleurs").iterdir()):
        meta = json.loads((d / "run.json").read_text(encoding="utf-8"))
        for setting in meta["settings"]:
            recs = [
                json.loads(x)
                for x in (d / f"{setting}.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            e = n = e_nd = n_nd = 0
            for r in recs:
                rw, hw = words(ref[r["file"]]), words(r["text"])
                err = edit_distance(rw, hw)
                e, n = e + err, n + len(rw)
                if not re.search(r"\d", ref[r["file"]]):
                    e_nd, n_nd = e_nd + err, n_nd + len(rw)
            rows.append({
                "system": d.name, "setting": setting, "clips": len(recs),
                "wer": round(100 * e / n, 2), "wer_no_digits": round(100 * e_nd / n_nd, 2),
                "no_digit_clips_words": n_nd, "segments_per_clip": round(sum(r["segments"] for r in recs) / len(recs), 2),
                "rtf": round(sum(r["ms"] for r in recs) / 1000 / sum(r["audio_s"] for r in recs), 4),
                "vram_added_mib": meta["vram_added_mib"],
            })  # fmt: skip
    (OUT / args.name / "fleurs" / "report.json").write_text(
        json.dumps(rows, indent=1), encoding="utf-8"
    )
    cols = [
        "system",
        "setting",
        "clips",
        "segments_per_clip",
        "wer",
        "wer_no_digits",
        "rtf",
        "vram_added_mib",
    ]
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(str(r[c]) for c in cols))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("unpack")
    r = sub.add_parser("run")
    r.add_argument("--system", required=True, choices=list(SYSTEMS))
    r.add_argument("--name", required=True)
    r.add_argument("--settings", nargs="+", default=list(SETTINGS), choices=list(SETTINGS))
    r.add_argument("--threads", type=int, default=0)
    p = sub.add_parser("report")
    p.add_argument("--name", required=True)
    args = ap.parse_args()
    return {"unpack": cmd_unpack, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
