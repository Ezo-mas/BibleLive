"""BibleLive real-world transcription benchmark runner.

Replays sermon recordings through the existing production pipeline
(audio frames -> VAD -> Whisper STT -> reference parser -> sender semantics)
and scores detected references against a gold annotation set.

Two modes:
  --mode rt      Real-time simulation: audio fed at original speed, preserving
                 30ms frame sizes, VAD pacing, utterance batching, and the
                 sender dedup window. This is the primary evaluation.
  --mode offline Diagnostic: same components but no real-time pacing (VAD still
                 segments; the real-time flow is removed). Never a substitute
                 for RT mode; used to isolate whether failures are
                 timing-related vs recognition-related.
  --mode windowed Evaluation-only: no VAD gating. The 16 kHz audio is sliced
                 into fixed windows (--window-s, default 30; --hop-s, default
                 15, i.e. 50% overlap). Each window is transcribed with the
                 same faster-whisper settings as offline mode, its Whisper
                 segments are fed in time order through the same StreamingParser,
                 and overlap duplicates are removed in this runner (see
                 dedupe_detections). Detection time = window start + Whisper
                 segment start. --skip-silent-windows (off by default) skips
                 windows below an RMS threshold as a compute saver.

Usage:
  python benchmarks/run_benchmark.py --manifest benchmarks/corpus/manifest.json \
      --audio-dir D:/sermons --mode rt --out results/rt-run.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from audio import stt as stt_module  # noqa: E402  (prompt/hotwords fingerprint for the cache key)
from audio.capture import AudioCapture  # noqa: E402  (frame sizing reference)
from audio.cli import add_tuning_arguments, apply_tuning_arguments  # noqa: E402
from audio.config import AudioPipelineConfig  # noqa: E402
from audio.metrics import PipelineMetrics  # noqa: E402
from audio.segmenter import FRAME_DURATION_MS, Segmenter, SegmentEvent  # noqa: E402
from audio.sender import ReferenceSender  # noqa: E402
from audio.stt import SpeechToText  # noqa: E402
from audio.vad import VoiceActivityDetector  # noqa: E402
from core.bible_lookup import BibleLookup  # noqa: E402
from core.verse_parser import Reference as ParsedReference  # noqa: E402
from core.verse_parser import ReferenceParser, StreamingParser  # noqa: E402

# Detection-vs-gold tolerance in seconds (allows for utterance batching lag).
LATENCY_TOLERANCE_S = 5.0
DEFAULT_GOLD_MATCH_FIELDS = ("book", "chapter", "verse_start", "verse_end")


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------


@dataclass
class GoldItem:
    record_id: str
    t_start_s: float
    t_end_s: float
    text_spoken: str
    book: str
    chapter: int
    verse_start: int | None
    verse_end: int | None
    verified_by: str = ""
    verified_date: str = ""


@dataclass
class Recording:
    id: str
    source: str
    file: str
    duration_s: float | None
    sha256: str | None
    language: str
    gold_file: str
    annotation_provenance: str
    audio_quality: str = ""
    notes: str = ""


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    with manifest_path.open(encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data


def load_gold(gold_path: Path, record_id: str) -> list[GoldItem]:
    items: list[GoldItem] = []
    with gold_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = json.loads(line)
            if d.get("record_id") != record_id:
                continue  # gold files may hold multiple recordings
            items.append(
                GoldItem(
                    record_id=record_id,
                    t_start_s=float(d["t_start_s"]),
                    t_end_s=float(d["t_end_s"]),
                    text_spoken=d.get("text_spoken", ""),
                    book=d["book"],
                    chapter=int(d["chapter"]),
                    verse_start=(
                        int(d["verse_start"]) if d.get("verse_start") is not None else None
                    ),
                    verse_end=int(d["verse_end"]) if d.get("verse_end") is not None else None,
                    verified_by=d.get("verified_by", ""),
                    verified_date=d.get("verified_date", ""),
                )
            )
    items.sort(key=lambda g: g.t_start_s)
    return items


# ---------------------------------------------------------------------------
# Detection / matching
# ---------------------------------------------------------------------------


@dataclass
class Detection:
    t_s: float
    book: str
    chapter: int
    verse_start: int | None
    verse_end: int | None
    confidence: str
    inferred: bool
    utterance_text: str = ""
    transcript_t_s: float | None = None


def ref_key(book: str, chapter: int, v1: int | None, v2: int | None) -> tuple:
    return (book.casefold(), chapter, v1, v2)


def match_detections_to_gold(
    detections: list[Detection], gold: list[GoldItem], tolerance_s: float = LATENCY_TOLERANCE_S
) -> tuple[list[tuple[Detection, GoldItem]], list[Detection], list[GoldItem]]:
    """Greedy match by reference key within the latency tolerance window.

    A detection matches a gold item when book/chapter/verse_start/verse_end all
    agree (a wrong verse is a miss, reported as an FP+FN pair — never partial
    credit) and the detection time is within tolerance of the gold midpoint.
    """
    matches: list[tuple[Detection, GoldItem]] = []
    used_det: set[int] = set()
    used_gold: set[int] = set()

    for gi, g in enumerate(gold):
        mid = (g.t_start_s + g.t_end_s) / 2
        best: tuple[float, int] | None = None  # (|latency|, detection index)
        for di, d in enumerate(detections):
            if di in used_det:
                continue
            if ref_key(d.book, d.chapter, d.verse_start, d.verse_end) != ref_key(
                g.book, g.chapter, g.verse_start, g.verse_end
            ):
                continue
            if abs(d.t_s - mid) > tolerance_s:
                continue
            dist = abs(d.t_s - mid)
            if best is None or dist < best[0]:
                best = (dist, di)
        if best is not None:
            used_det.add(best[1])
            used_gold.add(gi)
            matches.append((detections[best[1]], g))

    fps = [d for i, d in enumerate(detections) if i not in used_det]
    fns = [g for i, g in enumerate(gold) if i not in used_gold]
    return matches, fps, fns


# ---------------------------------------------------------------------------
# Stub receiver: captures what ReferenceSender would POST, without a live server
# ---------------------------------------------------------------------------


@dataclass
class StubReceiver:
    """Collects (arrival_time_s, reference) pairs in place of POST /api/reference."""

    t0: float
    received: list[tuple[float, ParsedReference]] = field(default_factory=list)
    rejected: list[tuple[float, ParsedReference]] = field(default_factory=list)
    # Dedup state mirroring ReferenceSender behavior across this run.
    recent: dict[str, float] = field(default_factory=dict)

    def accept(self, ref: ParsedReference) -> None:
        self.received.append((time.time() - self.t0, ref))

    def reject(self, ref: ParsedReference) -> None:
        self.rejected.append((time.time() - self.t0, ref))


class BenchmarkingSender(ReferenceSender):
    """ReferenceSender with delivery swapped for an in-process capture.

    Keeps dedup windows and retry policy intact (production behavior) — only
    the HTTP call is replaced by recording the would-be submission.
    """

    def __init__(self, stub: StubReceiver, config: AudioPipelineConfig, metrics: PipelineMetrics):
        # Bypass ReferenceSender.__init__ HTTP fields; reuse its dedup logic.
        self.server_url = "stub://benchmark"
        self.config = config
        self.metrics = metrics
        self.recent_references: dict[str, float] = {}
        self.stub = stub

    def send(self, ref) -> None:  # noqa: A003 (signature differs from parent on purpose)
        if self._is_duplicate(ref):
            self.metrics.references_deduplicated += 1
            self.stub.reject(ref)
            return
        self.stub.accept(ref)
        self.metrics.references_sent += 1


# ---------------------------------------------------------------------------
# Pipeline replay
# ---------------------------------------------------------------------------


def read_wav_16k_mono(path: Path) -> tuple[bytes, float]:
    """Read a WAV as 16kHz mono s16le PCM bytes, resampling via the wave module
    header only (resampling is done by the caller with ffmpeg when needed)."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(
                f"{path}: need 16kHz mono 16-bit WAV. Convert with:\n"
                f"  ffmpeg -i {path} -ar 16000 -ac 1 -c:a pcm_s16le {path.with_suffix('.16k.wav')}"
            )
        frames = w.readframes(w.getnframes())
        duration = w.getnframes() / w.getframerate()
    return frames, duration


def pcm_resample(path: Path, out_path: Path, tmp_dir: Path) -> Path:
    """Convert any audio file to 16kHz mono s16le via ffmpeg (if not already)."""
    import subprocess

    out_path = tmp_dir / (path.stem + ".16k.wav")
    # Reuse only a conversion at least as new as its source: a re-exported
    # source with the same name must not be shadowed by the old resample.
    if out_path.exists() and out_path.stat().st_mtime >= path.stat().st_mtime:
        return out_path
    # Convert to a side file and rename, so an interrupted ffmpeg can never
    # leave a truncated WAV that later runs would reuse as if complete.
    part_path = out_path.with_name(out_path.stem + ".part.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(part_path),
        ],
        check=True,
    )
    part_path.replace(out_path)
    return out_path


@dataclass
class RunResult:
    record_id: str
    mode: str
    detections: list[Detection]
    transcripts: list[tuple[float, str]]
    vad_events: list[tuple[float, str]]  # (t, "start"/"end"/"drop"/"blip")
    runtime_s: float
    audio_duration_s: float
    queue_dropped: int
    config_snapshot: dict[str, Any]


# ---------------------------------------------------------------------------
# Transcript/feed cache: skip STT on repeat runs
#
# STT is ~95% of a run. Parser/TTL/validator experiments never change a
# transcript, so runs cache the parser feed events keyed by content hash +
# model + segmentation settings, and replay them. Scoring is exact: replay
# uses the same helper as the live paths.
# ---------------------------------------------------------------------------

CACHE_VERSION = 2

# Like the live pipeline, drop references to verses that do not exist
# (BibleLookup's validator hook); set in main() from --bible-db. Parsing is
# not part of the transcript cache key, so cached runs validate too.
_VALIDATOR: Callable[[ParsedReference], bool] | None = None


def parse_t_range(spec: str | None) -> tuple[float, float] | None:
    """Parse 'START:END' seconds (empty END = file end). None disables."""
    if not spec:
        return None
    start_s, _, end_s = spec.partition(":")
    start = float(start_s or 0.0)
    end = float(end_s) if end_s else float("inf")
    if start < 0 or not end > start:
        raise ValueError(f"bad --t-range {spec!r}: want START:END with 0 <= START < END")
    return start, end


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verified_audio_sha(rec: Recording, audio_path: Path) -> str:
    """Hash the audio on disk and check it against the manifest.

    The hash is part of the transcript-cache key. Trusting a stale manifest
    value would replay transcripts decoded from different audio under the same
    key, so a mismatch stops the run instead of producing quietly wrong
    numbers. Hashing costs a fraction of a second per recording.
    """
    actual = sha256_file(audio_path)
    if rec.sha256 and rec.sha256 != actual:
        raise SystemExit(
            f"{rec.id}: {audio_path.name} does not match the manifest sha256\n"
            f"  manifest: {rec.sha256}\n  on disk:  {actual}\n"
            "The audio changed after it was hashed. Re-run benchmarks/hash_audio.py and "
            "update the manifest (and expect cached transcripts for it to be redone)."
        )
    return actual


def stt_prompt_fingerprint(language: str) -> str:
    """Short hash of the decoder prompt + hotwords STT will use for ``language``.

    Read from ``audio.stt`` at call time so an edited prompt invalidates cached
    transcripts instead of silently replaying ones decoded under the old text.
    """
    payload = json.dumps(
        [
            stt_module.WHISPER_INITIAL_PROMPTS.get(language, ""),
            stt_module.WHISPER_HOTWORDS.get(language, ""),
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def feed_cache_key(
    *,
    record_id: str,
    audio_sha256: str | None,
    language: str,
    model: str,
    device: str,
    compute_type: str | None,
    mode: str,
    beam_size: int = 5,
    window_s: float | None = None,
    hop_s: float | None = None,
    skip_silent: bool = False,
    silence_rms: float | None = None,
    context_ttl_s: float | None = 60.0,
    t_range: tuple[float, float] | None = None,
    segmentation: dict[str, int] | None = None,
) -> dict[str, Any]:
    key = {
        "cache_version": CACHE_VERSION,
        "record_id": record_id,
        "audio_sha256": audio_sha256,
        "language": language,
        "whisper_model": model,
        "whisper_device": device,
        "whisper_compute_type": compute_type,
        "stt_prompt": stt_prompt_fingerprint(language),
        "beam_size": beam_size,
        "mode": mode,
        "window_s": window_s,
        "hop_s": hop_s,
        "skip_silent_windows": skip_silent,
        "silence_rms": silence_rms,
        "context_ttl_s": context_ttl_s,
        "t_range": list(t_range) if t_range else None,
    }
    # VAD modes only; omitted for windowed so its existing cache entries stay valid.
    if segmentation is not None:
        key["segmentation"] = segmentation
    return key


def feed_cache_path(cache_dir: Path, key: dict[str, Any]) -> Path:
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", key["record_id"])
    return cache_dir / f"{safe_id}.{digest}.json"


def write_feed_cache(
    path: Path,
    key: dict[str, Any],
    *,
    units: list[list[tuple[float, str]]],
    transcripts: list[tuple[float, str]],
    audio_duration_s: float,
    stats: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "key": key,
                "units": [[[t, txt] for t, txt in unit] for unit in units],
                "transcripts": [[t, txt] for t, txt in transcripts],
                "audio_duration_s": audio_duration_s,
                "stats": stats or {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def read_feed_cache(path: Path) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("key", {}).get("cache_version") != CACHE_VERSION:
        return None
    return data


def register_feeds(
    parser: StreamingParser,
    sender: BenchmarkingSender | None,
    feeds: list[tuple[float, str]],
    detections: list[Detection],
) -> None:
    """Feed one dedup-scope group of (t_s, text) through parser + sender.

    One utterance (offline/rt) or one window (windowed) is one group: refs
    repeat within the group only. Shared by the live paths and cache replay
    so both build bit-identical detections. ``sender`` is None where the
    live path bypasses submission (windowed mode relies on its own dedup).
    """
    seen: set[tuple] = set()
    for t_s, text in feeds:
        for ref in parser.feed(text, t_s):
            key = ref_key(ref.book, ref.chapter, ref.verse_start, ref.verse_end)
            if key in seen:
                continue
            seen.add(key)
            detections.append(
                Detection(
                    t_s=t_s,
                    book=ref.book,
                    chapter=ref.chapter,
                    verse_start=ref.verse_start,
                    verse_end=ref.verse_end,
                    confidence=ref.confidence,
                    inferred=ref.inferred,
                    utterance_text=text,
                    transcript_t_s=t_s,
                )
            )
            if sender is not None:
                sender.send(ref)


def release_held(
    parser: StreamingParser,
    sender: BenchmarkingSender | None,
    units: list[Any],
    detections: list[Detection],
) -> None:
    """End of recording: report a chapter-only ref the parser is still holding.

    An empty feed releases it (nothing in it can name the verse), timed at the
    last feed. Live and cache-replay paths both call this, so they stay equal.
    """
    last_t = float(units[-1][-1][0]) if units and units[-1] else 0.0
    register_feeds(parser, sender, [(last_t, "")], detections)


def run_single_recording(
    rec: Recording,
    audio_path: Path,
    mode: str,
    config: AudioPipelineConfig,
    tmp_dir: Path,
    context_ttl_s: float | None = 60.0,
    use_cache: bool = True,
    cache_dir: Path | None = None,
    t_range: tuple[float, float] | None = None,
) -> RunResult:
    """Replay one recording through the production components.

    RT mode feeds 30ms VAD frames on the real wallclock; offline mode feeds
    them as fast as STT can keep up. Both use the real VAD+STT+parser+sender
    semantics. The control server is disabled either way.

    With a cache hit the VAD/STT stages are skipped and recorded feed
    events replay through a fresh parser+sender (bit-identical detections).
    t_range=(start_s, end_s) restricts processing to that span, in file
    coordinates; iteration-only, gold outside the span simply misses.
    """
    audio_sha = verified_audio_sha(rec, audio_path)
    # The live segmenter's settings; part of the cache key, since changing any
    # of them changes which audio Whisper sees.
    segmentation = {
        "vad_aggressiveness": config.vad_aggressiveness,
        "frame_duration_ms": FRAME_DURATION_MS,
        "end_of_speech_s": config.end_of_speech_s,
        "min_speech_s": config.min_speech_s,
        "soft_utterance_s": config.soft_utterance_s,
        "max_utterance_s": config.max_utterance_s,
    }
    key = feed_cache_key(
        record_id=rec.id,
        audio_sha256=audio_sha,
        language=rec.language,
        model=config.whisper_model,
        device=config.whisper_device,
        compute_type=config.whisper_compute_type,
        mode=mode,
        beam_size=config.beam_size,
        context_ttl_s=context_ttl_s,
        t_range=t_range,
        segmentation=segmentation,
    )
    cpath = feed_cache_path(cache_dir, key) if cache_dir is not None and use_cache else None

    parser = StreamingParser(
        ReferenceParser(language=rec.language, validator=_VALIDATOR),
        context_ttl_s=context_ttl_s,
        hold_chapter_only=True,  # as live
    )
    metrics = PipelineMetrics()
    stub = StubReceiver(t0=time.time())
    sender = BenchmarkingSender(stub, config, metrics)
    detections: list[Detection] = []
    transcripts: list[tuple[float, str]] = []
    vad_events: list[tuple[float, str]] = []
    frame_duration_ms = FRAME_DURATION_MS
    frame_bytes = AudioCapture().sample_rate * 2 * frame_duration_ms // 1000  # 960 bytes
    config_snapshot = {
        "whisper_model": config.whisper_model,
        "whisper_device": config.whisper_device,
        "whisper_compute_type": config.whisper_compute_type,
        **segmentation,
        "dedup_window_seconds": config.dedup_window_seconds,
        "mode": mode,
        "context_ttl_s": context_ttl_s,
        "transcripts_cached": False,
        "t_range": list(t_range) if t_range else None,
    }

    if cpath is not None and cpath.is_file():
        cached = read_feed_cache(cpath)
        if cached is not None and cached.get("key") == key:
            t0 = time.time()
            for unit in cached["units"]:
                register_feeds(parser, sender, [(float(t), txt) for t, txt in unit], detections)
            release_held(parser, sender, cached["units"], detections)
            transcripts.extend((float(t), txt) for t, txt in cached["transcripts"])
            runtime_s = time.time() - t0
            config_snapshot["transcripts_cached"] = True
            print(f"[cache] {rec.id}: replayed {len(cached['units'])} feed units, STT skipped")
            return RunResult(
                record_id=rec.id,
                mode=mode,
                detections=detections,
                transcripts=transcripts,
                vad_events=vad_events,
                runtime_s=runtime_s,
                audio_duration_s=float(cached["audio_duration_s"]),
                queue_dropped=0,
                config_snapshot=config_snapshot,
            )
        # Stale or unreadable cache falls through to a live run.

    pcm_path = pcm_resample(audio_path, audio_path, tmp_dir)
    pcm, _full_duration = read_wav_16k_mono(pcm_path)

    vad = VoiceActivityDetector(aggressiveness=config.vad_aggressiveness)
    stt = SpeechToText(
        model_size=config.whisper_model,
        device=config.whisper_device,
        language=rec.language,
        compute_type=config.whisper_compute_type,
        beam_size=config.beam_size,
        cpu_threads=config.cpu_threads,
    )

    # The live pipeline's segmenter, so benchmark and live segmentation cannot drift.
    segmenter = Segmenter.from_config(vad.is_speech, config)
    units: list[list[tuple[float, str]]] = []

    def utterance_end(t_start: float, event: SegmentEvent) -> None:
        # Mirrors pipeline._queue_speech_segment + _process_utterance.
        vad_events.append((t_start, event.kind))
        if event.kind == "blip":
            return
        text = stt.transcribe_utterance(b"".join(event.frames), rec.language).text
        if text is None:
            transcripts.append((t_start, "<none>"))
            return
        transcripts.append((t_start, text))
        units.append([(t_start, text)])
        register_feeds(parser, sender, [(t_start, text)], detections)

    n_frames = len(pcm) // frame_bytes
    frame_s = frame_duration_ms / 1000.0
    first_frame, last_frame = 0, n_frames
    if t_range is not None:
        first_frame = min(n_frames, int(t_range[0] / frame_s))
        if t_range[1] != float("inf"):
            last_frame = min(n_frames, int(t_range[1] / frame_s))
    audio_duration = (last_frame - first_frame) * frame_s
    t0 = time.time()
    audio_t = first_frame * frame_s  # simulated audio position for offline pacing
    frame_idx = first_frame
    queue_dropped = 0
    while frame_idx < last_frame:
        frame = pcm[frame_idx * frame_bytes : (frame_idx + 1) * frame_bytes]
        frame_idx += 1
        if mode == "rt":
            # Real-time pacing: replay at original speed.
            target = t0 + (frame_idx - first_frame) * frame_duration_ms / 1000.0
            delay = target - time.time()
            if delay > 0:
                time.sleep(delay)
        audio_t += frame_duration_ms / 1000.0
        # Wallclock in rt mode preserves STT-lag timing; file coordinates
        # when slicing (iteration-only) so gold still matches.
        t_s = audio_t if (mode != "rt" or t_range is not None) else time.time() - t0

        event = segmenter.push(frame)
        if event is None:
            continue
        if event.kind == "start":
            vad_events.append((t_s, "start"))
        else:
            utterance_end(t_s - event.trailing_silent_frames * frame_duration_ms / 1000.0, event)

    final = segmenter.flush()
    if final is not None:
        utterance_end(last_frame * frame_s, final)
    release_held(parser, sender, units, detections)

    runtime_s = time.time() - t0
    if cache_dir is not None:
        write_feed_cache(
            feed_cache_path(cache_dir, key),
            key,
            units=units,
            transcripts=transcripts,
            audio_duration_s=audio_duration,
        )
    return RunResult(
        record_id=rec.id,
        mode=mode,
        detections=detections,
        transcripts=transcripts,
        vad_events=vad_events,
        runtime_s=runtime_s,
        audio_duration_s=audio_duration,
        queue_dropped=queue_dropped,
        config_snapshot=config_snapshot,
    )


# ---------------------------------------------------------------------------
# Windowed mode (evaluation only; no VAD gating)
# ---------------------------------------------------------------------------

WINDOW_DEFAULT_S = 30.0
HOP_DEFAULT_S = 15.0
SILENCE_RMS_DEFAULT = 0.002  # float samples in [-1, 1] (about -54 dBFS)
SAMPLE_RATE = 16000


def window_bounds(
    n_samples: int, sample_rate: int, window_s: float, hop_s: float
) -> list[tuple[int, int]]:
    """Return ``(start, end)`` sample indices of fixed windows covering the audio.

    Windows start every ``hop_s`` seconds and last ``window_s`` seconds (the last
    one is clipped to the end of the audio). Iteration stops once a window
    reaches the end of the audio, so no window is fully contained in another.
    """
    if window_s <= 0 or hop_s <= 0:
        raise ValueError("window_s and hop_s must be positive")
    if hop_s > window_s:
        raise ValueError("hop_s must not exceed window_s (audio would be skipped)")
    win = round(window_s * sample_rate)
    hop = round(hop_s * sample_rate)
    if win <= 0 or hop <= 0:
        raise ValueError("window/hop too small for the sample rate")

    bounds: list[tuple[int, int]] = []
    start = 0
    while start < n_samples:
        end = min(start + win, n_samples)
        bounds.append((start, end))
        if end >= n_samples:
            break
        start += hop
    return bounds


def is_silent_window(samples: np.ndarray, rms_threshold: float) -> bool:
    """True when the window's RMS (float samples in [-1, 1]) is below the threshold."""
    if samples.size == 0:
        return True
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return rms < rms_threshold


def dedupe_detections(detections: list[Detection], window_s: float) -> list[Detection]:
    """Count a reference once when overlapping windows re-detect it.

    Detections are considered in time order. A detection is dropped when the
    same (book, chapter, verse_start, verse_end) was already kept less than
    ``window_s`` seconds earlier. Repeats farther apart than one window length
    are kept, since a preacher can legitimately cite the same verse again.
    """
    kept: list[Detection] = []
    last_kept_t: dict[tuple, float] = {}
    for d in sorted(detections, key=lambda x: x.t_s):
        key = ref_key(d.book, d.chapter, d.verse_start, d.verse_end)
        prev = last_kept_t.get(key)
        if prev is not None and d.t_s - prev < window_s:
            continue
        last_kept_t[key] = d.t_s
        kept.append(d)
    return kept


@dataclass
class WindowStats:
    windows_total: int = 0
    windows_skipped_silent: int = 0
    windows_failed: int = 0
    raw_detections: int = 0


def _transcribe_window_segments(
    stt: SpeechToText, chunk: np.ndarray, language: str
) -> list[tuple[float | None, str]]:
    """Transcribe one window and return ``(segment_start_s, text)`` pieces.

    ``SpeechToText.transcribe_utterance`` joins Whisper's segments and discards
    their timestamps, so this calls ``stt.model.transcribe`` directly with the
    same decoding kwargs as ``transcribe_utterance`` in ``backend/audio/stt.py``.
    The text post-processing (``_fix_common_transcription_errors`` and
    ``_collapse_repetition``) is the production code, called read-only.

    ``segment_start_s`` is relative to the start of ``chunk``. If a repetition
    loop was trimmed, the timestamps are no longer reliable and ``None`` is
    returned for the single remaining piece.
    """
    if chunk.size < SAMPLE_RATE * 0.5:  # same minimum as transcribe_utterance
        return []
    segments, _info = stt.model.transcribe(
        chunk,
        language=language,
        initial_prompt=stt.initial_prompt,
        **({"hotwords": stt.hotwords} if stt.hotwords else {}),
        beam_size=stt.beam_size,
        best_of=5,
        temperature=0.0,
        compression_ratio_threshold=stt_module.COMPRESSION_RATIO_THRESHOLD,
        log_prob_threshold=stt_module.LOG_PROB_THRESHOLD,
        no_speech_threshold=stt_module.NO_SPEECH_THRESHOLD,
        condition_on_previous_text=False,
        repetition_penalty=1.05,
        no_repeat_ngram_size=6,
    )
    pieces: list[tuple[float | None, str]] = []
    for seg in segments:
        text = stt._fix_common_transcription_errors(seg.text.strip())
        if text:
            pieces.append((float(seg.start), text))
    if not pieces:
        return []
    joined = " ".join(t for _, t in pieces)
    collapsed, loop = stt._collapse_repetition(joined)
    if loop:
        return [(None, collapsed)]
    return pieces


def run_windowed_recording(
    rec: Recording,
    audio_path: Path,
    config: AudioPipelineConfig,
    tmp_dir: Path,
    window_s: float,
    hop_s: float,
    skip_silent: bool,
    silence_rms: float,
    context_ttl_s: float | None = 60.0,
    use_cache: bool = True,
    cache_dir: Path | None = None,
    t_range: tuple[float, float] | None = None,
) -> RunResult:
    """Evaluate one recording with fixed overlapping windows instead of VAD.

    Windows are processed in time order through one StreamingParser so that
    continuations across window and segment boundaries still resolve. Each
    Whisper segment is fed to the parser separately, which lets each detection
    carry the time of the segment it came from. Detection time is
    ``window_start + segment_start``; it falls back to ``window_start`` when the
    segment timestamp is unavailable (repetition-loop trimming).

    Cache and t_range behave as in :func:`run_single_recording`.
    """
    audio_sha = verified_audio_sha(rec, audio_path)
    key = feed_cache_key(
        record_id=rec.id,
        audio_sha256=audio_sha,
        language=rec.language,
        model=config.whisper_model,
        device=config.whisper_device,
        compute_type=config.whisper_compute_type,
        mode="windowed",
        beam_size=config.beam_size,
        window_s=window_s,
        hop_s=hop_s,
        skip_silent=skip_silent,
        silence_rms=silence_rms,
        context_ttl_s=context_ttl_s,
        t_range=t_range,
    )
    cpath = feed_cache_path(cache_dir, key) if cache_dir is not None and use_cache else None

    parser = StreamingParser(
        ReferenceParser(language=rec.language, validator=_VALIDATOR),
        context_ttl_s=context_ttl_s,
        hold_chapter_only=True,  # as live
    )
    raw: list[Detection] = []
    transcripts: list[tuple[float, str]] = []
    config_snapshot = {
        "whisper_model": config.whisper_model,
        "whisper_device": config.whisper_device,
        "whisper_compute_type": config.whisper_compute_type,
        "mode": "windowed",
        "window_s": window_s,
        "hop_s": hop_s,
        "skip_silent_windows": skip_silent,
        "silence_rms_threshold": silence_rms if skip_silent else None,
        "dedup_rule": "same book/chapter/verse_start/verse_end within window_s",
        "timestamp_rule": "window_start + whisper segment start (fallback window_start)",
        "context_ttl_s": context_ttl_s,
        "transcripts_cached": False,
        "t_range": list(t_range) if t_range else None,
    }

    if cpath is not None and cpath.is_file():
        cached = read_feed_cache(cpath)
        if cached is not None and cached.get("key") == key:
            t0 = time.time()
            for unit in cached["units"]:
                register_feeds(parser, None, [(float(t), txt) for t, txt in unit], raw)
            release_held(parser, None, cached["units"], raw)
            transcripts.extend((float(t), txt) for t, txt in cached["transcripts"])
            cached_stats = cached.get("stats", {})
            stats = WindowStats(
                windows_total=int(cached_stats.get("windows_total", 0)),
                windows_skipped_silent=int(cached_stats.get("windows_skipped_silent", 0)),
                windows_failed=int(cached_stats.get("windows_failed", 0)),
            )
            detections = dedupe_detections(raw, window_s)
            stats.raw_detections = len(raw)
            config_snapshot.update(
                {
                    "windows_total": stats.windows_total,
                    "windows_skipped_silent": stats.windows_skipped_silent,
                    "windows_failed": stats.windows_failed,
                    "raw_detections_before_dedup": stats.raw_detections,
                    "transcripts_cached": True,
                }
            )
            runtime_s = time.time() - t0
            config_snapshot["transcripts_cached"] = True
            print(f"[cache] {rec.id}: replayed {len(cached['units'])} windows, STT skipped")
            return RunResult(
                record_id=rec.id,
                mode="windowed",
                detections=detections,
                transcripts=transcripts,
                vad_events=[],
                runtime_s=runtime_s,
                audio_duration_s=float(cached["audio_duration_s"]),
                queue_dropped=0,
                config_snapshot=config_snapshot,
            )
        # Stale or unreadable cache falls through to a live run.

    pcm_path = pcm_resample(audio_path, audio_path, tmp_dir)
    pcm, full_duration = read_wav_16k_mono(pcm_path)
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

    stt = SpeechToText(
        model_size=config.whisper_model,
        device=config.whisper_device,
        language=rec.language,
        compute_type=config.whisper_compute_type,
        beam_size=config.beam_size,
        cpu_threads=config.cpu_threads,
    )
    bounds = window_bounds(len(samples), SAMPLE_RATE, window_s, hop_s)
    if t_range is not None:
        lo, hi = t_range
        bounds = [(s, e) for s, e in bounds if s / SAMPLE_RATE >= lo and s / SAMPLE_RATE < hi]
        span_end = min(full_duration, hi) if hi != float("inf") else full_duration
        audio_duration = max(0.0, span_end - max(0.0, lo))
    else:
        audio_duration = full_duration
    stats = WindowStats(windows_total=len(bounds))
    units: list[list[tuple[float, str]]] = []

    t0 = time.time()
    window_times_s: list[float] = []  # wall time per transcribed window (throttling check)
    for start, end in bounds:
        w_start_s = start / SAMPLE_RATE
        chunk = samples[start:end]
        if skip_silent and is_silent_window(chunk, silence_rms):
            stats.windows_skipped_silent += 1
            transcripts.append((w_start_s, "<silent>"))
            continue
        try:
            w_t0 = time.time()
            pieces = _transcribe_window_segments(stt, chunk, rec.language)
            window_times_s.append(round(time.time() - w_t0, 3))
        except Exception as e:  # keep the run going; the failure is counted and printed
            stats.windows_failed += 1
            print(f"       [window {w_start_s:.1f}s] transcription failed: {e}", file=sys.stderr)
            continue
        if not pieces:
            transcripts.append((w_start_s, "<none>"))
            continue
        transcripts.append((w_start_s, " ".join(t for _, t in pieces)))
        feeds = [
            (w_start_s + seg_start if seg_start is not None else w_start_s, text)
            for seg_start, text in pieces
        ]
        units.append(feeds)
        register_feeds(parser, None, feeds, raw)
    release_held(parser, None, units, raw)
    runtime_s = time.time() - t0

    detections = dedupe_detections(raw, window_s)
    stats.raw_detections = len(raw)
    config_snapshot.update(
        {
            "windows_total": stats.windows_total,
            "windows_skipped_silent": stats.windows_skipped_silent,
            "windows_failed": stats.windows_failed,
            "raw_detections_before_dedup": stats.raw_detections,
            "window_times_s": window_times_s,
        }
    )
    if cache_dir is not None:
        write_feed_cache(
            feed_cache_path(cache_dir, key),
            key,
            units=units,
            transcripts=transcripts,
            audio_duration_s=audio_duration,
            stats={
                "windows_total": stats.windows_total,
                "windows_skipped_silent": stats.windows_skipped_silent,
                "windows_failed": stats.windows_failed,
            },
        )
    return RunResult(
        record_id=rec.id,
        mode="windowed",
        detections=detections,
        transcripts=transcripts,
        vad_events=[],
        runtime_s=runtime_s,
        audio_duration_s=audio_duration,
        queue_dropped=0,
        config_snapshot=config_snapshot,
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_recording(
    result: RunResult, gold: list[GoldItem], tolerance_s: float = LATENCY_TOLERANCE_S
) -> dict[str, Any]:
    matches, fps, fns = match_detections_to_gold(result.detections, gold, tolerance_s)
    tp = len(matches)
    prec = tp / (tp + len(fps)) if (tp + len(fps)) else 0.0
    rec = tp / (tp + len(fns)) if (tp + len(fns)) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    latencies = [d.t_s - (g.t_start_s + g.t_end_s) / 2 for d, g in matches]
    return {
        "record_id": result.record_id,
        "mode": result.mode,
        "audio_duration_s": round(result.audio_duration_s, 2),
        "runtime_s": round(result.runtime_s, 2),
        "rtf": round(result.runtime_s / result.audio_duration_s, 3)
        if result.audio_duration_s
        else None,
        "gold_count": len(gold),
        "detections": len(result.detections),
        "tp": tp,
        "fp": len(fps),
        "fn": len(fns),
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(f1, 3),
        "latency_s": {
            "mean": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "max": round(max(latencies), 2) if latencies else None,
        },
        "matches": [
            {
                "gold_t_s": g.t_start_s,
                "detected_t_s": round(d.t_s, 2),
                "latency_s": round(d.t_s - (g.t_start_s + g.t_end_s) / 2, 2),
                "reference": f"{d.book} {d.chapter}:{d.verse_start}"
                + (f"-{d.verse_end}" if d.verse_end else ""),
                "utterance": d.utterance_text[:160],
            }
            for d, g in matches
        ],
        "false_positives": [
            {
                "t_s": round(d.t_s, 2),
                "reference": f"{d.book} {d.chapter}:{d.verse_start}",
                "utterance": d.utterance_text[:160],
            }
            for d in fps
        ],
        "missed": [
            {"t_start_s": g.t_start_s, "reference": f"{g.book} {g.chapter}:{g.verse_start}"}
            for g in fns
        ],
        "vad_events": [],  # trimmed in JSON output to keep files small
        "transcripts": [{"t_s": round(t, 2), "text": txt} for t, txt in result.transcripts],
        "queue_dropped": result.queue_dropped,
        "config": result.config_snapshot,
        "failures": [],
    }


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    """Weighted mean across recordings (weighted by gold count)."""
    total_gold = sum(s["gold_count"] for s in scores)
    total_tp = sum(s["tp"] for s in scores)
    total_fp = sum(s["fp"] for s in scores)
    total_fn = sum(s["fn"] for s in scores)
    prec = total_tp / (total_tp + total_fp) if total_tp + total_fp else 0.0
    rec = total_tp / (total_tp + total_fn) if total_tp + total_fn else 0.0
    return {
        "recordings": len(scores),
        "total_gold": total_gold,
        "total_tp": total_tp,
        "total_fp": total_fp,
        "total_fn": total_fn,
        "precision_prec_micro": round(prec, 3),
        "recall_micro": round(rec, 3),
        "f1_micro": round(2 * prec * rec / (prec + rec), 3) if (prec + rec) else 0.0,
        "per_recording": {s["record_id"]: s for s in scores},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--audio-dir", default=None, help="directory containing audio (or env var)")
    ap.add_argument("--mode", choices=["rt", "offline", "windowed"], default="rt")
    ap.add_argument(
        "--window-s",
        type=float,
        default=WINDOW_DEFAULT_S,
        help="windowed mode: window length in seconds (default 30)",
    )
    ap.add_argument(
        "--hop-s",
        type=float,
        default=HOP_DEFAULT_S,
        help="windowed mode: hop between window starts in seconds (default 15)",
    )
    ap.add_argument(
        "--skip-silent-windows",
        action="store_true",
        help="windowed mode: skip windows whose RMS is below --silence-rms (default off)",
    )
    ap.add_argument(
        "--silence-rms",
        type=float,
        default=SILENCE_RMS_DEFAULT,
        help="RMS threshold for --skip-silent-windows, float samples in [-1, 1]",
    )
    ap.add_argument(
        "--context-ttl-s",
        type=float,
        default=60.0,
        help="max age (s) of the parser book/chapter context; <=0 disables expiry",
    )
    ap.add_argument(
        "--no-cache",
        action="store_true",
        help="skip the transcript cache and always run STT (a fresh run still refreshes it)",
    )
    ap.add_argument(
        "--cache-dir",
        default="results/.bench-cache",
        help="directory for cached STT feed events (default results/.bench-cache)",
    )
    ap.add_argument(
        "--t-range",
        default=None,
        help="restrict processing to START:END seconds, e.g. 0:600 (iteration-only)",
    )
    ap.add_argument(
        "--bible-db",
        default=str(REPO_ROOT / "bible_data" / "biblelive.sqlite"),
        help="Bible SQLite DB for the live pipeline's verse-exists check (skipped if missing)",
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--records", default=None, help="comma-separated subset of record ids")
    ap.add_argument(
        "--device",
        default=None,
        help="Whisper device override (e.g. cpu when CUDA BLAS DLLs are unavailable)",
    )
    ap.add_argument("--model", default=None, help="Whisper model size override")
    add_tuning_arguments(ap)  # the live CLI's tuning flags, e.g. --max-utterance-s
    ap.add_argument(
        "--compute-type", default=None, help="Whisper compute type override (e.g. int8 on cpu)"
    )
    args = ap.parse_args()

    global _VALIDATOR
    if Path(args.bible_db).is_file():
        _VALIDATOR = BibleLookup(args.bible_db, readonly=True).get_validator_hook()
    else:
        print(
            f"[warn] {args.bible_db} not found: references to non-existent verses are kept "
            "(the live pipeline drops them)",
            file=sys.stderr,
        )

    audio_dir_env = Path(__import__("os").environ.get("BIBLELIVE_BENCH_AUDIO_DIR", ""))
    audio_dir = Path(args.audio_dir) if args.audio_dir else audio_dir_env

    manifest = load_manifest(Path(args.manifest))
    config = AudioPipelineConfig(control_enabled=False)  # no language-control server
    if args.device:
        config.whisper_device = args.device
    if args.model:
        config.whisper_model = args.model
    if args.compute_type:
        config.whisper_compute_type = args.compute_type
    apply_tuning_arguments(config, args)
    tmp_dir = audio_dir / ".bench-16k" if audio_dir else Path("bench_audio/.bench-16k")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        t_range = parse_t_range(args.t_range)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Reproducibility info.
    try:
        import ctranslate2
        import faster_whisper  # noqa: F401

        ct2_version = ctranslate2.__version__
    except Exception:
        ct2_version = "unknown"

    results: list[dict[str, Any]] = []
    for rec_dict in manifest["recordings"]:
        rec = Recording(**{k: rec_dict.get(k) for k in Recording.__dataclass_fields__})
        if args.records and rec.id not in args.records.split(","):
            continue
        audio_path = (audio_dir / rec.file) if audio_dir and rec.file else None
        if audio_path is None or not audio_path.is_file():
            print(
                f"[skip] {rec.id}: audio not found ({rec.file}); add via --audio-dir "
                "or BIBLELIVE_BENCH_AUDIO_DIR",
                file=sys.stderr,
            )
            continue
        gold = (
            load_gold(Path(args.manifest).parent / rec.gold_file, rec.id) if rec.gold_file else []
        )
        print(f"[run ] {rec.id} ({rec.duration_s or '?'}s, gold={len(gold)}) ...")
        context_ttl = args.context_ttl_s if args.context_ttl_s > 0 else None
        use_cache = not args.no_cache
        if args.mode == "windowed":
            run = run_windowed_recording(
                rec,
                audio_path,
                config,
                tmp_dir,
                args.window_s,
                args.hop_s,
                args.skip_silent_windows,
                args.silence_rms,
                context_ttl,
                use_cache,
                cache_dir,
                t_range,
            )
        else:
            run = run_single_recording(
                rec,
                audio_path,
                args.mode,
                config,
                tmp_dir,
                context_ttl,
                use_cache,
                cache_dir,
                t_range,
            )
        score = score_recording(run, gold)
        results.append(score)
        print(
            f"       P={score['precision']} R={score['recall']} "
            f"TP={score['tp']} FP={score['fp']} FN={score['fn']} "
            f"RTF={score['rtf']}"
        )

    out: dict[str, Any] = {
        "benchmark_version": 1,
        "mode": args.mode,
        "manifest": str(args.manifest),
        "audio_dir": str(audio_dir) if audio_dir else None,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tolerance_s": LATENCY_TOLERANCE_S,
        "verse_validator": _VALIDATOR is not None,
        "dependency_versions": {"ctranslate2": ct2_version},
        "aggregate": aggregate(results),
        "recordings": results,
    }
    if args.mode == "windowed":
        out["window_s"] = args.window_s
        out["hop_s"] = args.hop_s
        out["skip_silent_windows"] = args.skip_silent_windows
        out["context_ttl_s"] = args.context_ttl_s
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"results written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
