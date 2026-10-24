"""Follow-along benchmark: the server's hint policy on replayed sermons, on a simulated clock.

Question: with partial transcripts of the utterance still being spoken
(``audio/partials.py``), sent while follow-along is armed, does the operator get
the next verse sooner, and what does it cost the transcription worker? Every
variant runs the same code path, ``--partials off`` included, so the comparison
is fair.

Per recording (lines of ``benchmarks/corpus/lt-sermons.txt``, t-ranges honoured):

- Segmentation: the live ``Segmenter`` over the live VAD, fed the recording's
  30 ms frames in file coordinates, as run_benchmark's offline mode does.
- Finals: the feed cache run_benchmark writes for ``--mode offline`` with the same
  settings (``offline_feed_key``). Finals are never transcribed here: a missing
  cache is an error. Re-segmented utterances are aligned with the cache's
  utterances in order (speech end times must agree within 0.05 s). Only the
  primary model's text is used, and only a confident one is posted, as live.
- Partials (``--partials on``): while the server says armed, the live
  ``PartialScheduler`` snapshots the open utterance into a ``PartialMailbox``
  (newest wins; dropped when its utterance closes, and skipped by the worker when the
  server has since said disarmed). ``PartialTranscript`` decides
  the audio (``--partial-mode``: grow or chunk). The audio is transcribed with the
  live engine (``make_stt``), cached on disk by its bytes (``--partial-cache-dir``),
  so variants that share audio share transcripts.
- Simulated clock, one worker (the transcription thread). A final costs
  ``--final-rtf`` x its audio seconds, a partial ``--partial-rtf`` x the audio it
  transcribes. A final is queued at the frame where the segmenter closed it. A
  free worker takes a waiting final first (FIFO), else the mailbox snapshot, and
  never preempts. A text is posted to the server when its cost has elapsed.
- Server: the shared ``FollowAlong`` (``core/server/follow_along.py``), fed each post
  at its arrival time. Its ``active`` flag is the VAD side's armed state, False
  until the first post.
- Display: each gold reference with a verse goes on screen at its ``t_end_s`` +
  ``--display-delay-s``. With ``--chain`` the simulated operator shows each hint
  ``--operator-delay-s`` after it arrives, unless a newer gold reference came in
  between; the shown passage is a new display, and it stays in the same chain.
- Truth, from confident finals only: verse V of (book, chapter) is read in final U
  if ``index.scores(book, chapter, U.text)[V] >= 0.5``. Each hint records the
  utterance it came from (a partial's snapshot utterance). Strict confirmation (the
  metric): V is read in the final of that same utterance, or else in the next final
  utterance when that one starts within 3 s of the source utterance's end. lag_s =
  arrival - start of the confirming final (the own one when both read V), and
  in_time = arrival <= its end. For each strictly confirmed (chain root gold item,
  V) only the first such hint counts. Loose confirmation (``confLoose``, reference
  only): some final within [t - 25, t + 15] reads V.

Metrics (per recording and totals, printed as a table and written to ``--out``,
which is never overwritten): hints, strict confirmed (conf) and unconfirmed, loose
confirmed (confLoose), distinct confirmed verses, lag median and p90, in-time count and share, partial jobs and posts,
partial audio seconds and as a multiple of the replayed audio, worker busy share,
and how long finals waited (over the finals that waited: median, p95, max; and how
many were behind a partial). Each hint is also listed in the JSON.

Timestamp truth (``--truth timestamps``; ``final``, the default, is unchanged and its
columns keep their meaning). Each confident final that starts inside a display period (from
a displayed gold reference's show time to the next one's, or the end) is transcribed again by
Kmynas for word timestamps, cached under ``<cache-dir>/follow-along-words`` (``words-v1``).
Word times are mapped from the utterance's audio to file time: utterance audio is the voiced
frames only, so each sample offset goes through the file frame index of its frame
(``positions``). ``benchmarks/verse_timing.py`` aligns each period's words, in order, to the
displayed chapter's verses (the displayed verse up to 10 verses past the display's end) and
gives each verse read (coverage >= ``--verse-read-bar``) a start (first aligned word) and an
end (last aligned word). A hint is confirmed when its verse was read with a start in
[arrival - 30 s, arrival + 10 s]. ``lagStartMed``/``lagStartP90`` are arrival minus that
start; ``beforeEnd`` counts the counted hints that arrived before the verse's end. Only the
first confirmed hint per (chain root, verse) is counted there. ``versesRead`` are the verses
read in display periods and ``hinted`` those of them with a confirmed hint (recall).

Usage (the canonical CPU tier, after the detection benchmark wrote the finals cache):
  python benchmarks/follow_along_bench.py --partials off --out results/follow-along/<name>.json \\
      --stt kmynas --device cpu --model large-v3-turbo --context-ttl-s 600 \\
      --kmynas-model ~/models/kmynas-v4/kmynas-v4/kmynas-v4-Q8_0.gguf --cpu-threads 4 \\
      --audio-dir bench_audio --cache-dir results/.bench-cache \\
      --bible-db <main checkout>/bible_data/biblelive.sqlite \\
      --verse-text-db <main checkout>/bible_data/bible_ltb.sqlite
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import heapq
import itertools
import json
import math
import os
import random
import sys
import time
import wave
from bisect import bisect_right
from collections import defaultdict, deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
# This checkout's packages, first: an installed copy of ``audio`` elsewhere must not win.
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from audio.cli import (  # noqa: E402
    add_partial_arguments,
    add_tuning_arguments,
    apply_tuning_arguments,
)
from audio.config import AudioPipelineConfig  # noqa: E402
from audio.partials import (  # noqa: E402
    PartialMailbox,
    PartialScheduler,
    PartialSnapshot,
    PartialTranscript,
)
from audio.segmenter import FRAME_DURATION_MS, Segmenter, SegmentEvent  # noqa: E402
from audio.vad import create_vad  # noqa: E402
from benchmarks.run_benchmark import (  # noqa: E402
    GoldItem,
    Recording,
    feed_cache_path,
    feeds_from_cache,
    load_gold,
    load_manifest,
    make_stt,
    offline_feed_key,
    parse_t_range,
    pcm_resample,
    read_feed_cache,
    read_wav_16k_mono,
    stt_cache_identity,
    verified_audio_sha,
)
from benchmarks.verse_timing import (  # noqa: E402
    SpokenWord,
    VerseRead,
    chapter_verse_texts,
    period_verse_reads,
)
from core.bible_lookup import BibleLookup  # noqa: E402
from core.server.follow_along import REGRESS_HOLD_S, FollowAlong, HintPolicy  # noqa: E402
from core.server.models import BibleReference  # noqa: E402
from core.verse_text import VerseTextIndex  # noqa: E402

DEFAULT_CORPUS = REPO_ROOT / "benchmarks" / "corpus" / "lt-sermons.txt"
SAMPLE_RATE = 16000
BYTES_PER_FRAME = SAMPLE_RATE * 2 * FRAME_DURATION_MS // 1000  # 16-bit mono: 960 bytes
BYTES_PER_S = SAMPLE_RATE * 2
FRAME_S = FRAME_DURATION_MS / 1000.0
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 480
# --truth timestamps: a hint is confirmed by a verse read with its start this close to it.
TS_BEFORE_S = 30.0
TS_AFTER_S = 10.0
WORDS_VERSION = "words-v1"  # the word-timestamp cache's version: bump on any change of the output
# Cached and re-segmented utterances must end at the same time (see CachedFinals).
PAIR_TOLERANCE_S = 0.05
# A hint is confirmed by a final that reads its verse within this window around it.
CONFIRM_BEFORE_S = 25.0
CONFIRM_AFTER_S = 15.0
# Strict confirmation also accepts the next final utterance, if it starts this soon after
# the hint's own utterance ends.
STRICT_NEXT_GAP_S = 3.0
# Share of a final's distinctive words that must explain a verse for it to count as read.
READ_BAR = 0.5
NONE_TEXT = "<none>"  # the cache's entry for an utterance the model heard nothing in
# Partials run the primary model alone (--stt kmynas, or the Kmynas of kmynas+canary).
PRIMARY_ENGINE = "kmynas"


@dataclass(frozen=True)
class CorpusEntry:
    manifest: Path
    record_id: str
    t_range: tuple[float, float] | None


def load_corpus(path: Path) -> list[CorpusEntry]:
    """``<manifest> <record> [START:END]`` lines; manifests are relative to the corpus file."""
    entries = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) not in (2, 3):
            raise ValueError(f"{path}: want '<manifest> <record> [START:END]', got {raw!r}")
        t_range = parse_t_range(parts[2]) if len(parts) == 3 else None
        entries.append(CorpusEntry(path.parent / parts[0], parts[1], t_range))
    return entries


def frame_bounds(n_frames: int, t_range: tuple[float, float] | None) -> tuple[int, int]:
    """The first and end frame of ``t_range``, as run_single_recording slices them."""
    first, last = 0, n_frames
    if t_range is not None:
        first = min(n_frames, int(t_range[0] / FRAME_S))
        if t_range[1] != math.inf:
            last = min(n_frames, int(t_range[1] / FRAME_S))
    return first, last


def load_pcm(audio_path: Path, audio_dir: Path, cache_dir: Path) -> bytes:
    """16 kHz mono PCM of a recording, as run_benchmark reads it.

    A recording that is not a 16 kHz mono WAV (an MP3, say) is read from the 16 kHz
    conversion the detection benchmark made in ``audio_dir/.bench-16k``, when that is
    as new as the source (pcm_resample's rule). Otherwise it is converted into the
    cache dir: bench_audio is read-only.
    """
    try:
        pcm, _ = read_wav_16k_mono(audio_path)
        return pcm
    except (SystemExit, wave.Error):
        pass
    for convert_dir in (audio_dir / ".bench-16k", cache_dir / ".bench-16k"):
        converted = convert_dir / (audio_path.stem + ".16k.wav")
        if converted.is_file() and converted.stat().st_mtime >= audio_path.stat().st_mtime:
            pcm, _ = read_wav_16k_mono(converted)
            return pcm
    convert_dir = cache_dir / ".bench-16k"
    convert_dir.mkdir(parents=True, exist_ok=True)
    pcm, _ = read_wav_16k_mono(pcm_resample(audio_path, audio_path, convert_dir))
    return pcm


class CachedFinals:
    """The cached finals of one recording, handed out in order: one per non-blip utterance.

    Each call takes the next cached utterance and checks it against the replay's
    end time. It returns (cached speech start or None, the text live would post,
    whether that text is confident). Two-model caches (Kmynas + Canary) hold both
    texts; as live (``pipeline._detect_references``), the first confident one of
    them (Kmynas, then Canary) is the post. Only a confident text reaches the truth.
    """

    def __init__(self, cached: dict[str, Any]):
        self._transcripts: list[list[Any]] = cached["transcripts"]
        self._units: list[list[list[Any]]] = cached["units"]
        self._next = 0
        self._unit = 0

    def __call__(self, end_s: float) -> tuple[float | None, str | None, bool]:
        if self._next >= len(self._transcripts):
            raise SystemExit("the replay closed more utterances than the finals cache holds")
        t_s, transcript = self._transcripts[self._next]
        self._next += 1
        if abs(float(t_s) - end_s) > PAIR_TOLERANCE_S:
            raise SystemExit(
                f"utterance {self._next}: the replay ends it at {end_s:.2f} s, the cache at "
                f"{float(t_s):.2f} s. Segmentation differs from the cached run."
            )
        if transcript == NONE_TEXT:
            return None, None, False
        feed = feeds_from_cache(self._units[self._unit])[0]
        self._unit += 1
        if len(feed) < 4:
            raise SystemExit(
                f"utterance {self._next}: the cache has no utterance starts; rerun the "
                "detection benchmark with --no-cache"
            )
        # One model: the transcript is its text. Two models: "primary || alternate".
        expected = feed[1] if len(feed) == 4 else f"{feed[1]} || {feed[4]}"
        if transcript != expected:
            raise SystemExit(f"utterance {self._next}: cache feed does not match its transcript")
        start_s = float(feed[2])
        candidates = [(feed[1], feed[3])]
        if len(feed) > 4:
            candidates.append((feed[4], feed[5]))
        for text, low in candidates:
            if text and not low:
                return start_s, str(text), True
        return start_s, (str(feed[1]) if feed[1] else None), False

    def finished(self) -> None:
        """Every cached utterance must have been replayed."""
        if self._next != len(self._transcripts) or self._unit != len(self._units):
            raise SystemExit(
                f"the replay closed {self._next} utterances, the finals cache has "
                f"{len(self._transcripts)}"
            )


class PartialTexts:
    """Partial transcripts of one recording: from disk when the audio was seen before,
    else from the live engine, which is loaded on first use.

    The engine is the primary GGUF model alone, on the run's device, whatever the
    finals use: partials never run the second model (live: primary only), so on the
    Kmynas + Canary tier they are Kmynas alone. The cache key carries the device and
    the engine, so CPU and GPU partial texts never mix.
    """

    def __init__(
        self,
        cache_dir: Path,
        record: Recording,
        audio_sha: str,
        config: AudioPipelineConfig,
    ):
        self._dir = cache_dir
        self._record = record
        self._audio_sha = audio_sha
        self._config = config
        self._engine_config = replace(config, stt_engine=PRIMARY_ENGINE)
        self._stt: Any = None
        self.transcribed = 0
        self.from_cache = 0

    def __call__(self, audio: bytes) -> tuple[str | None, bool]:
        """(text, confident) for the audio. Only a confident text is posted, as live."""
        key = {
            "cache_version": 1,
            "record_id": self._record.id,
            "audio_sha256": self._audio_sha,
            "language": self._record.language,
            "partial_engine": {
                "device": self._config.whisper_device,
                "stt": stt_cache_identity(self._engine_config),
            },
            "frames": len(audio) // BYTES_PER_FRAME,
            # Frames of a non-contiguous utterance (keep_pauses off) have no position in
            # the file, so the audio itself identifies them.
            "pcm_sha256": hashlib.sha256(audio).hexdigest(),
        }
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
        safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in self._record.id)
        path = self._dir / f"{safe_id}.{digest}.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.from_cache += 1
            return data["text"], bool(data["confident"])
        if self._stt is None:
            self._stt = make_stt(self._engine_config, self._record.language)
        result = self._stt.transcribe_utterance(audio, self._record.language)
        if result.failed:
            raise SystemExit(f"{self._record.id}: partial transcription failed (see the log)")
        text = result.text or None
        confident = bool(text) and result.low_confidence_reason is None
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "key": key,
                    "text": text,
                    "confident": confident,
                    "low": result.low_confidence_reason,
                    "duration_ms": round(result.duration_ms, 1),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.transcribed += 1
        if self.transcribed % 25 == 0:
            print(
                f"[partial] {self._record.id}: {self.transcribed} transcribed, "
                f"{self.from_cache} from cache",
                flush=True,
            )
        return text, confident


@dataclass
class Final:
    """A closed utterance, queued for the worker as a final."""

    index: int  # position among the finals (the cache's order)
    utterance: int  # the segmenter's utterance number (blips included in the count)
    start_s: float  # speech start
    end_s: float  # speech end: the time the cache and the hint truth use
    close_s: float  # the frame where the segmenter closed it: queued then
    audio_s: float  # audio the worker transcribes for it
    text: str | None  # primary model's text, None when it heard nothing
    confident: bool  # trusted: posted to the server and used for truth
    behind_partial: bool = False  # the worker was running a partial when it closed


@dataclass
class Hint:
    """A hint the server returned, at its arrival time."""

    t_s: float
    source: str  # "final" or "partial": the transcript that produced it
    utterance: int  # the utterance it came from (a partial's snapshot utterance)
    book: str
    chapter: int
    verse: int
    verse_end: int
    follows: str  # the passage on screen it follows, for the report
    root: int  # gold item at the root of the display chain
    shown: bool = False  # the simulated operator showed it
    confirmed: bool = False  # strict: see confirm_hints
    confirmed_loose: bool = False  # the reference window rule, kept for comparison
    nearest: int | None = None  # index of the strict confirming final
    lag_s: float | None = None  # arrival - start of that final
    in_time: bool | None = None  # arrival <= end of that final
    counted: bool = False  # the first strict hint of its (chain root, verse)
    ts_read: VerseRead | None = None  # --truth timestamps: the verse reading it answers
    ts_counted: bool = False  # the first timestamp-confirmed hint of its (chain root, verse)
    ts_lag_s: float | None = None  # arrival - the verse's start
    ts_in_time: bool | None = None  # arrival <= the verse's end


@dataclass
class ReplayResult:
    record_id: str
    replay_s: float
    gold_count: int
    displayed: int
    finals: list[Final]
    hints: list[Hint]
    partial_jobs: int
    partial_posts: int
    partial_audio_s: float
    final_busy_s: float
    partial_busy_s: float
    waits: list[tuple[float, bool]]  # (time a final waited, whether it was behind a partial)
    # Speech end of every closed utterance, blips included, by utterance number.
    utterance_ends: dict[int, float] = field(default_factory=dict)
    # (first strict hint, its confirming final) per (chain root, verse): the counted ones.
    pairs: list[tuple[Hint, Final]] = field(default_factory=list)
    # --truth timestamps: the verses read in this recording's display periods.
    verse_reads: list[VerseRead] = field(default_factory=list)


def make_vad(config: AudioPipelineConfig) -> Any:
    """A fresh VAD (they are stateful: each replay needs its own)."""
    return create_vad(
        config.vad,
        aggressiveness=config.vad_aggressiveness,
        silero_threshold=config.silero_threshold,
        silero_neg_threshold=config.silero_neg_threshold,
    )


def _ref_text(book: str, chapter: int, verse_start: int | None, verse_end: int | None) -> str:
    if verse_start is None:
        return f"{book} {chapter}"
    text = f"{book} {chapter}:{verse_start}"
    return text + (f"-{verse_end}" if verse_end and verse_end != verse_start else "")


class FollowAlongReplay:
    """One recording on the simulated clock. See the module docstring for the model."""

    def __init__(
        self,
        *,
        record_id: str,
        language: str,
        gold: Sequence[GoldItem],
        feed: Callable[[float], tuple[float | None, str | None, bool]],
        index: VerseTextIndex,
        bible: BibleLookup,
        server: FollowAlong,
        config: AudioPipelineConfig,
        vad_is_speech: Callable[[bytes], bool],
        transcribe_partial: Callable[[bytes], tuple[str | None, bool]] | None,
        final_rtf: float,
        partial_rtf: float,
        display_delay_s: float,
        operator_delay_s: float,
        chain: bool,
    ):
        self.record_id = record_id
        self.language = language
        self._gold = list(gold)
        self._feed = feed
        self._index = index
        self._bible = bible
        self._server = server
        self._partials_on = config.partial_transcripts
        self._segmenter = Segmenter.from_config(vad_is_speech, config)
        self._scheduler = PartialScheduler.from_config(config)
        self._mailbox = PartialMailbox()
        self._pending: tuple[PartialSnapshot, float] | None = None  # mirrors the mailbox
        self._partial_tx = PartialTranscript(config.partial_mode)
        self._transcribe_partial = transcribe_partial
        self._final_rtf = final_rtf
        self._partial_rtf = partial_rtf
        self._operator_delay_s = operator_delay_s
        self._chain = chain
        self._armed = False  # the server's latest answer; False before any post
        self._utterance = 0
        self._speech_start = 0.0
        self._finals: list[Final] = []
        self._utterance_ends: dict[int, float] = {}
        self._final_queue: deque[Final] = deque()
        self._free_at = 0.0
        self._last_job: tuple[float, float, str] | None = None
        self._busy = {"final": 0.0, "partial": 0.0}
        self._partial_jobs = 0
        self._partial_posts = 0
        self._partial_audio_s = 0.0
        self._waits: list[tuple[float, bool]] = []
        self._events: list[tuple[float, int, str, Any]] = []
        self._seq = itertools.count()
        self._hints: list[Hint] = []
        self._display: BibleReference | None = None
        self._roots: dict[str, int] = {}  # display id -> gold item at its chain root
        self._last_gold_s = -math.inf
        self._displayed = 0
        for i, item in enumerate(self._gold):
            if item.verse_start is not None:
                self._displayed += 1
                self._push(item.t_end_s + display_delay_s, "gold", i)

    def run(self, pcm: bytes, first_frame: int, last_frame: int) -> ReplayResult:
        """Replay frames [first_frame, last_frame) and drain the worker and the posts."""
        # Same clock arithmetic as run_single_recording, so cached times agree exactly.
        audio_t = first_frame * FRAME_S
        for frame_idx in range(first_frame, last_frame):
            frame = pcm[frame_idx * BYTES_PER_FRAME : (frame_idx + 1) * BYTES_PER_FRAME]
            audio_t += FRAME_DURATION_MS / 1000.0
            # Work due before this frame sees only the frames already pushed.
            self._advance(audio_t, jobs_at_t=False)
            self._push_frame(frame, audio_t)
            # Work created by this frame (or due exactly at it) sees it too.
            self._advance(audio_t, jobs_at_t=True)
        end_s = last_frame * FRAME_S
        self._advance(end_s, jobs_at_t=False)
        event = self._segmenter.flush()
        if event is not None:
            self._close(event, close_s=end_s, end_s=end_s)
        self._advance(math.inf, jobs_at_t=True)
        return ReplayResult(
            record_id=self.record_id,
            replay_s=(last_frame - first_frame) * FRAME_S,
            gold_count=len(self._gold),
            displayed=self._displayed,
            finals=self._finals,
            hints=self._hints,
            partial_jobs=self._partial_jobs,
            partial_posts=self._partial_posts,
            partial_audio_s=self._partial_audio_s,
            final_busy_s=self._busy["final"],
            partial_busy_s=self._busy["partial"],
            waits=self._waits,
            utterance_ends=self._utterance_ends,
        )

    # -- the VAD thread -------------------------------------------------------

    def _push_frame(self, frame: bytes, t: float) -> None:
        event = self._segmenter.push(frame)
        if event is not None and event.kind != "start":
            self._close(
                event,
                close_s=t,
                end_s=t - event.trailing_silent_frames * FRAME_DURATION_MS / 1000.0,
            )
        elif event is not None:
            self._utterance += 1
            self._speech_start = t - event.onset_frames * FRAME_S
        # The scheduler is told about every frame; the armed state comes from the server.
        if self._partials_on and self._scheduler.due(self._segmenter, event) and self._armed:
            snapshot = PartialSnapshot(
                self._utterance, self.language, tuple(self._segmenter.speech_frames)
            )
            self._mailbox.put(snapshot)
            self._pending = (snapshot, t)

    def _close(self, event: SegmentEvent, close_s: float, end_s: float) -> None:
        # The final covers this utterance from now on, so its waiting partial is stale.
        self._mailbox.discard(self._utterance)
        if self._pending is not None and self._pending[0].utterance == self._utterance:
            self._pending = None
        self._utterance_ends[self._utterance] = end_s
        if event.kind != "end":
            return  # a blip is never transcribed, live or here
        cached_start, text, confident = self._feed(end_s)
        final = Final(
            index=len(self._finals),
            utterance=self._utterance,
            start_s=cached_start if cached_start is not None else self._speech_start,
            end_s=end_s,
            close_s=close_s,
            audio_s=len(event.frames) * FRAME_S,
            text=text,
            confident=confident and bool(text),
            behind_partial=self._busy_at(close_s),
        )
        self._finals.append(final)
        self._final_queue.append(final)

    def _busy_at(self, t: float) -> bool:
        last = self._last_job
        return last is not None and last[2] == "partial" and last[0] <= t < last[1]

    # -- the worker -----------------------------------------------------------

    def _next_job(self) -> tuple[float, str] | None:
        """When the free worker would start next, and what: a waiting final beats a
        partial that could start no later."""
        options = []
        if self._final_queue:
            options.append((max(self._free_at, self._final_queue[0].close_s), 0, "final"))
        if self._pending is not None:
            options.append((max(self._free_at, self._pending[1]), 1, "partial"))
        if not options:
            return None
        start, _, kind = min(options)
        return start, kind

    def _start_job(self, start: float, kind: str) -> None:
        if kind == "final":
            final = self._final_queue.popleft()
            self._waits.append((start - final.close_s, final.behind_partial))
            cost = self._final_rtf * final.audio_s
            self._begin(start, cost, "final")
            if final.confident:
                self._push(start + cost, "post", ("final", final.text, final.utterance))
            return
        snapshot = self._mailbox.take()
        self._pending = None
        # Disarmed since the snapshot was taken: skipped, as live (pipeline._process_partial).
        if snapshot is None or self._transcribe_partial is None or not self._armed:
            return
        audio = self._partial_tx.audio_for(snapshot)
        if audio is None:  # chunk mode: nothing new since the last partial
            return
        text, confident = self._transcribe_partial(audio)
        posted = self._partial_tx.add(snapshot, text if confident else None)
        audio_s = len(audio) / BYTES_PER_S
        cost = self._partial_rtf * audio_s
        self._partial_jobs += 1
        self._partial_audio_s += audio_s
        self._begin(start, cost, "partial")
        if posted is not None:
            self._push(start + cost, "post", ("partial", posted, snapshot.utterance))

    def _begin(self, start: float, cost: float, kind: str) -> None:
        self._free_at = start + cost
        self._busy[kind] += cost
        self._last_job = (start, start + cost, kind)

    # -- the server, the display and the operator -----------------------------

    def _post(self, t: float, source: str, text: str, utterance: int) -> None:
        hint, armed = self._server.on_transcript(self._index, self._bible, self._display, text, t)
        self._armed = armed
        if source == "partial":
            self._partial_posts += 1
        display = self._display
        if hint is None or display is None:
            return
        self._hints.append(
            Hint(
                t_s=t,
                source=source,
                utterance=utterance,
                book=hint.book,
                chapter=hint.chapter,
                verse=hint.verse_start,
                verse_end=hint.verse_end,
                follows=_ref_text(
                    display.book, display.chapter, display.verse_start, display.verse_end
                ),
                root=self._roots[display.id],
            )
        )
        if self._chain:
            self._push(t + self._operator_delay_s, "show", len(self._hints) - 1)

    def _show_hint(self, position: int) -> None:
        hint = self._hints[position]
        if self._last_gold_s > hint.t_s:
            return  # a newer gold reference came in between: the operator keeps it
        self._set_display(
            BibleReference(
                book=hint.book,
                chapter=hint.chapter,
                verse_start=hint.verse,
                verse_end=hint.verse_end,
            ),
            hint.root,
        )
        hint.shown = True

    def _show_gold(self, t: float, position: int) -> None:
        item = self._gold[position]
        self._last_gold_s = t
        self._set_display(
            BibleReference(
                book=item.book,
                chapter=item.chapter,
                verse_start=item.verse_start,
                verse_end=item.verse_end,
            ),
            position,
        )

    def _set_display(self, display: BibleReference, root: int) -> None:
        self._display = display
        self._roots[display.id] = root

    # -- the clock -------------------------------------------------------------

    def _push(self, time_s: float, kind: str, payload: Any) -> None:
        heapq.heappush(self._events, (time_s, next(self._seq), kind, payload))

    def _advance(self, t: float, jobs_at_t: bool) -> None:
        """Run every event at or before ``t`` and every worker start before ``t``, in
        time order (a start at exactly ``t`` only when ``jobs_at_t``)."""
        while True:
            event_time = self._events[0][0] if self._events else math.inf
            job = self._next_job()
            job_time = job[0] if job is not None else math.inf
            if self._events and event_time <= t and event_time <= job_time:
                _, _, kind, payload = heapq.heappop(self._events)
                if kind == "gold":
                    self._show_gold(event_time, payload)
                elif kind == "post":
                    source, text, utterance = payload
                    self._post(event_time, source, text, utterance)
                else:
                    self._show_hint(payload)
            elif job is not None and (job_time < t or (jobs_at_t and job_time <= t)):
                self._start_job(job_time, job[1])
            else:
                return


def confirm_hints(
    hints: list[Hint],
    finals: Sequence[Final],
    index: VerseTextIndex,
    utterance_ends: dict[int, float],
    read_bar: float = READ_BAR,
) -> list[tuple[Hint, Final]]:
    """Confirm each hint, strictly (the metric) and loosely (for reference), and return
    the counted pairs: the first strict hint of each (chain root, verse) with its
    confirming final.

    Strict: the hinted verse scores >= ``read_bar`` in the confident final of the
    hint's own utterance, or else in the next final utterance, but only when that one
    starts within STRICT_NEXT_GAP_S of the source utterance's end. The own final is
    preferred when both read the verse. lag_s and in_time come from that final.
    Loose: some confident final whose speech lies within CONFIRM_BEFORE_S before to
    CONFIRM_AFTER_S after the hint reads the verse (the window rule of the first
    version, kept as ``confirmed_loose``).
    """
    confident = [f for f in finals if f.confident]
    by_utterance = {f.utterance: f for f in finals}
    scored: dict[tuple[int, str, int], dict[int, float]] = {}

    def reads(final: Final, hint: Hint) -> bool:
        key = (final.index, hint.book, hint.chapter)
        if key not in scored:
            scored[key] = index.scores(hint.book, hint.chapter, final.text or "")
        return scored[key].get(hint.verse, 0.0) >= read_bar

    for hint in hints:
        hint.confirmed = hint.confirmed_loose = False
        hint.nearest = hint.lag_s = hint.in_time = None
        hint.counted = False
        own = by_utterance.get(hint.utterance)
        following = next((f for f in finals if f.utterance > hint.utterance), None)
        source_end = utterance_ends.get(hint.utterance)
        strict: Final | None = None
        if own is not None and own.confident and reads(own, hint):
            strict = own
        elif (
            following is not None
            and following.confident
            and source_end is not None
            and following.start_s <= source_end + STRICT_NEXT_GAP_S
            and reads(following, hint)
        ):
            strict = following
        hint.confirmed_loose = any(
            f.end_s >= hint.t_s - CONFIRM_BEFORE_S
            and f.start_s <= hint.t_s + CONFIRM_AFTER_S
            and reads(f, hint)
            for f in confident
        )
        if strict is not None:
            hint.confirmed = True
            hint.nearest = strict.index
            hint.lag_s = hint.t_s - strict.start_s
            hint.in_time = hint.t_s <= strict.end_s
    counted: dict[tuple[int, str, int, int], tuple[Hint, Final]] = {}
    for hint in hints:
        if hint.confirmed and hint.nearest is not None:
            key = (hint.root, hint.book, hint.chapter, hint.verse)
            if key not in counted:
                hint.counted = True
                counted[key] = (hint, finals[hint.nearest])
    return list(counted.values())


# -- timestamp truth (--truth timestamps) --------------------------------------------


@dataclass(frozen=True)
class ClosedUtterance:
    """An utterance the segmenter closed, as ``FollowAlongReplay`` closes it."""

    number: int  # 1-based over the closed utterances, blips included
    kind: str  # "end" (a final) or "blip" (too short to transcribe)
    end_s: float  # speech end: the time the finals cache and the hints use
    speech_start_s: float  # the segmenter's onset; the cache's start wins when it has one
    frames: list[bytes]  # exactly the audio the final is transcribed from
    positions: list[int]  # the file frame index of each of ``frames``


def _frame_positions(frames: Sequence[bytes], recent: Sequence[tuple[int, bytes]]) -> list[int]:
    """The file frame index of each of ``frames``, matched by object identity.

    The segmenter keeps the frame objects it was pushed (it copies no bytes), so identity
    finds each frame's place in the file exactly, where equal bytes would not: two silent
    frames are the same bytes.
    """
    by_id = {id(frame): index for index, frame in recent}
    try:
        return [by_id[id(frame)] for frame in frames]
    except KeyError:
        raise RuntimeError("an utterance frame fell out of the recent-frame window") from None


def closed_utterances(
    pcm: bytes,
    first: int,
    last: int,
    config: AudioPipelineConfig,
    is_speech: Callable[[bytes], bool],
) -> Iterator[ClosedUtterance]:
    """Segment frames [first, last) as ``FollowAlongReplay`` does and yield each closed utterance."""
    segmenter = Segmenter.from_config(is_speech, config)
    # Enough recent frames for any utterance's audio: the length cap, pre-roll and post-roll.
    keep = (
        segmenter.max_utterance_frames
        + segmenter.pre_roll_frames
        + segmenter.start_window_frames
        + segmenter.post_roll_frames
        + 2
    )
    recent: deque[tuple[int, bytes]] = deque(maxlen=keep)
    speech_start_s = 0.0
    number = 0
    for frame_idx in range(first, last):
        frame = pcm[frame_idx * BYTES_PER_FRAME : (frame_idx + 1) * BYTES_PER_FRAME]
        recent.append((frame_idx, frame))
        now_s = (frame_idx + 1) * FRAME_S
        event = segmenter.push(frame)
        if event is None:
            continue
        if event.kind == "start":
            speech_start_s = now_s - event.onset_frames * FRAME_S
            continue
        number += 1
        end_s = now_s - event.trailing_silent_frames * FRAME_S
        positions = _frame_positions(event.frames, recent)
        yield ClosedUtterance(number, event.kind, end_s, speech_start_s, event.frames, positions)
    event = segmenter.flush()
    if event is not None:
        number += 1
        positions = _frame_positions(event.frames, recent)
        yield ClosedUtterance(
            number, event.kind, last * FRAME_S, speech_start_s, event.frames, positions
        )


def utterance_time_s(sample: int, positions: Sequence[int], *, end: bool = False) -> float:
    """File time (s) of a sample offset in an utterance's audio.

    ``positions[k]`` is the file frame index of the utterance's k-th frame, and a frame is
    ``SAMPLES_PER_FRAME`` samples. A word's end (``end=True``) falls in the frame that holds
    its last sample, so it does not run on over the silence dropped before the next frame.
    """
    last = len(positions) - 1
    if end:
        frame = min(max(sample - 1, 0) // SAMPLES_PER_FRAME, last)
    else:
        frame = min(max(sample, 0) // SAMPLES_PER_FRAME, last)
    offset = min(max(sample - frame * SAMPLES_PER_FRAME, 0), SAMPLES_PER_FRAME)
    return positions[frame] * FRAME_S + offset / SAMPLE_RATE


class WordTimer:
    """Kmynas word timestamps of utterances, cached on disk by their audio (``words-v1``).

    The primary model alone, always on the CPU: the truth is the same whichever tier a run
    simulates, so both tiers share one cache, and with word timestamps on, transcribe.cpp's
    Vulkan backend ran the decoder about 15x slower than the CPU (2026-10-22, GTX 1070).
    The model loads on the first cache miss, so a fully cached run never loads it.
    """

    def __init__(self, cache_dir: Path, config: AudioPipelineConfig):
        self._dir = cache_dir / "follow-along-words"
        self._config = replace(config, stt_engine=PRIMARY_ENGINE, whisper_device="cpu")
        self._session: Any = None
        self._errors: Any = None
        self.transcribed = 0
        self.from_cache = 0

    def __call__(self, record_id: str, language: str, pcm: bytes) -> list[tuple[str, int, int]]:
        """(word, start ms, end ms) of ``pcm`` (16 kHz mono int16), as the model times it."""
        key = {
            "version": WORDS_VERSION,
            "record_id": record_id,
            "audio_sha256": hashlib.sha256(pcm).hexdigest(),
            "language": language,
            "model": {
                "device": self._config.whisper_device,
                "stt": stt_cache_identity(self._config),
            },
        }
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
        safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in record_id)
        path = self._dir / f"{safe_id}.{digest}.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.from_cache += 1
            return [(str(text), int(t0), int(t1)) for text, t0, t1 in data["words"]]
        words = self._transcribe(pcm, language)
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"key": key, "words": [list(w) for w in words]}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.transcribed += 1
        if self.transcribed % 50 == 0:
            print(
                f"[words] {record_id}: {self.transcribed} transcribed, "
                f"{self.from_cache} from cache",
                flush=True,
            )
        return words

    def _transcribe(self, pcm: bytes, language: str) -> list[tuple[str, int, int]]:
        if self._session is None:
            import transcribe_cpp  # the gguf extra, as the live Kmynas engine

            backend: Literal["auto", "cpu"] = (
                "cpu" if self._config.whisper_device == "cpu" else "auto"
            )
            model = transcribe_cpp.Model(str(self._config.kmynas_model), backend=backend)
            self._session = model.session(n_threads=self._config.cpu_threads)
            self._errors = transcribe_cpp.errors
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        try:
            result = self._session.run(memoryview(audio), language=language, timestamps="word")
        except self._errors.OutputRepetition as e:
            result = e.partial_result  # a decoder loop: one copy is kept, as the live path does
        if result is None:
            return []
        return [(w.text, int(w.t0_ms), int(w.t1_ms)) for w in result.words]


@dataclass(frozen=True)
class DisplayPeriod:
    """A displayed gold reference on screen: from its show time to the next show time."""

    start_s: float
    end_s: float  # the next displayed reference's show time, or the end of the replay
    book: str
    chapter: int
    verse_start: int
    verse_end: int | None


def display_periods(gold: Sequence[GoldItem], display_delay_s: float) -> list[DisplayPeriod]:
    """The display periods the replay shows: the gold references with a verse, by show time."""
    shown: list[tuple[float, int, str, int, int, int | None]] = []
    for index, item in enumerate(gold):
        if item.verse_start is not None:
            show_s = item.t_end_s + display_delay_s
            shown.append((show_s, index, item.book, item.chapter, item.verse_start, item.verse_end))
    shown.sort(key=lambda s: (s[0], s[1]))
    periods = []
    for k, (start_s, _, book, chapter, verse_start, verse_end) in enumerate(shown):
        end_s = shown[k + 1][0] if k + 1 < len(shown) else math.inf
        periods.append(DisplayPeriod(start_s, end_s, book, chapter, verse_start, verse_end))
    return periods


def spoken_by_period(
    pcm: bytes,
    first: int,
    last: int,
    config: AudioPipelineConfig,
    is_speech: Callable[[bytes], bool],
    feed: CachedFinals,
    periods: Sequence[DisplayPeriod],
    words: Callable[[bytes], list[tuple[str, int, int]]],
) -> list[list[SpokenWord]]:
    """The timed words of the confident finals that start in each display period.

    ``feed`` is a fresh finals cache, consumed in the replay's order (the segmentation
    must match it). ``words`` times one utterance's audio. Only confident finals are timed:
    they are the only utterances the truth uses.
    """
    starts = [period.start_s for period in periods]
    spoken: list[list[SpokenWord]] = [[] for _ in periods]
    for closed in closed_utterances(pcm, first, last, config, is_speech):
        if closed.kind != "end":
            continue
        cached_start, _, confident = feed(closed.end_s)
        start_s = closed.speech_start_s if cached_start is None else cached_start
        index = bisect_right(starts, start_s) - 1
        if not confident or index < 0 or start_s >= periods[index].end_s:
            continue
        audio = b"".join(closed.frames)
        for text, start_ms, end_ms in words(audio):
            spoken[index].append(
                SpokenWord(
                    text,
                    utterance_time_s(start_ms * SAMPLE_RATE // 1000, closed.positions),
                    utterance_time_s(end_ms * SAMPLE_RATE // 1000, closed.positions, end=True),
                    closed.number,
                )
            )
    return spoken


def read_periods(
    periods: Sequence[DisplayPeriod],
    spoken: Sequence[Sequence[SpokenWord]],
    verse_db: str,
    read_bar: float,
) -> list[VerseRead]:
    """The verses read aloud in each display period, aligned to its chapter's text."""
    texts: dict[tuple[str, int], dict[int, str]] = {}
    reads: list[VerseRead] = []
    for period, words in zip(periods, spoken, strict=True):
        if not words:
            continue
        key = (period.book, period.chapter)
        if key not in texts:
            texts[key] = chapter_verse_texts(verse_db, period.book, period.chapter)
        for read in period_verse_reads(
            book=period.book,
            chapter=period.chapter,
            spoken=words,
            texts=texts[key],
            verse_start=period.verse_start,
            verse_end=period.verse_end,
            read_bar=read_bar,
        ):
            reads.append(replace(read, period_s=period.start_s))
    return reads


def confirm_by_timestamps(
    hints: list[Hint],
    reads: Sequence[VerseRead],
    before_s: float = TS_BEFORE_S,
    after_s: float = TS_AFTER_S,
) -> list[Hint]:
    """Confirm each hint by the reading of its verse; return the counted hints.

    Confirmed: the hint's verse was read with its start in [arrival - before_s,
    arrival + after_s]; of several such readings, the nearest start. Lag is arrival minus
    that start, and in time means arrival <= the reading's end. Counted: the first
    confirmed hint of each (chain root, verse), as the strict lag columns count them.
    """
    by_verse: dict[tuple[str, int, int], list[VerseRead]] = defaultdict(list)
    for read in reads:
        by_verse[(read.book, read.chapter, read.verse)].append(read)
    seen: set[tuple[int, str, int, int]] = set()
    counted: list[Hint] = []
    for hint in hints:
        hint.ts_read = None
        hint.ts_counted = False
        hint.ts_lag_s = hint.ts_in_time = None
        candidates = [
            r
            for r in by_verse.get((hint.book, hint.chapter, hint.verse), [])
            if hint.t_s - before_s <= r.start_s <= hint.t_s + after_s
        ]
        if not candidates:
            continue
        at = hint.t_s
        read = min(candidates, key=lambda r: abs(r.start_s - at))
        hint.ts_read = read
        hint.ts_lag_s = hint.t_s - read.start_s
        hint.ts_in_time = hint.t_s <= read.end_s
        key = (hint.root, hint.book, hint.chapter, hint.verse)
        if key not in seen:
            seen.add(key)
            hint.ts_counted = True
            counted.append(hint)
    return counted


def timestamp_metrics(replays: Sequence[ReplayResult]) -> dict[str, Any]:
    """The --truth timestamps columns of one or more recordings (totals from the raw lists)."""
    hints = [h for r in replays for h in r.hints]
    counted = [h for h in hints if h.ts_counted]
    lags = [h.ts_lag_s for h in counted if h.ts_lag_s is not None]
    before_end = sum(1 for h in counted if h.ts_in_time)
    reads = {(r.record_id, v.book, v.chapter, v.verse) for r in replays for v in r.verse_reads}
    hinted = {
        (r.record_id, h.book, h.chapter, h.verse)
        for r in replays
        for h in r.hints
        if h.ts_read is not None
    }

    def rounded(value: float | None) -> float | None:
        return None if value is None else round(value, 2)

    return {
        "confirmed_ts": sum(1 for h in hints if h.ts_read is not None),
        "lag_start_n": len(lags),
        "lag_start_median_s": rounded(percentile(lags, 50)),
        "lag_start_p90_s": rounded(percentile(lags, 90)),
        "before_end": before_end,
        "before_end_share": round(before_end / len(lags), 3) if lags else None,
        "verses_read": len(reads),
        "hinted": len(hinted & reads),
    }


def summarize(replays: Sequence[ReplayResult], truth: str) -> dict[str, Any]:
    """metrics(), plus the timestamp columns when the truth is ``timestamps``."""
    out = metrics(replays)
    if truth == "timestamps":
        out.update(timestamp_metrics(replays))
    return out


def sample_reads(replays: Sequence[ReplayResult], n: int, seed: int) -> list[tuple[str, VerseRead]]:
    """``n`` verse readings drawn at random (seeded) from all recordings, for checking by eye."""
    pool = [(r.record_id, read) for r in replays for read in r.verse_reads]
    return random.Random(seed).sample(pool, min(n, len(pool)))


def render_samples(samples: Sequence[tuple[str, VerseRead]]) -> str:
    lines = []
    for number, (record_id, read) in enumerate(samples, 1):
        ref = _ref_text(read.book, read.chapter, read.verse, None)
        lines.append(f"#{number} {record_id}  {ref}  (display shown at {read.period_s:.1f} s)")
        lines.append(
            f"   coverage {read.matched}/{read.tokens} = {read.coverage:.2f}   "
            f"start {read.start_s:.1f} s   end {read.end_s:.1f} s"
        )
        lines.append(f"   verse: {read.text}")
        heard = ", ".join(
            f"{spoken} {start:.1f}-{end:.1f}" for _, spoken, start, end in read.aligned
        )
        lines.append(f"   heard: {heard}")
    return "\n".join(lines)


def percentile(values: Sequence[float], q: float) -> float | None:
    """The q-th percentile by linear interpolation; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100.0
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def metrics(replays: Sequence[ReplayResult]) -> dict[str, Any]:
    """The metrics of one or more recordings; totals are computed from the raw lists."""
    hints = [h for r in replays for h in r.hints]
    confirmed = [h for h in hints if h.confirmed]
    pairs = [pair for r in replays for pair in r.pairs]
    lags = [h.lag_s for h, _ in pairs if h.lag_s is not None]
    in_time = sum(1 for h, _ in pairs if h.in_time)
    replay_s = sum(r.replay_s for r in replays)
    partial_audio_s = sum(r.partial_audio_s for r in replays)
    busy_s = sum(r.final_busy_s + r.partial_busy_s for r in replays)
    waits = [w for r in replays for w, _ in r.waits if w > 0]
    distinct = {
        (r.record_id, h.book, h.chapter, h.verse) for r in replays for h in r.hints if h.confirmed
    }
    waited_behind = sum(1 for r in replays for w, behind in r.waits if w > 0 and behind)

    def rounded(value: float | None, digits: int = 2) -> float | None:
        return None if value is None else round(value, digits)

    return {
        "replay_s": round(replay_s, 1),
        "gold": sum(r.gold_count for r in replays),
        "displayed_gold": sum(r.displayed for r in replays),
        "finals": sum(len(r.finals) for r in replays),
        "finals_confident": sum(1 for r in replays for f in r.finals if f.confident),
        "hints": len(hints),
        "confirmed": len(confirmed),
        "unconfirmed": len(hints) - len(confirmed),
        "confirmed_loose": sum(1 for h in hints if h.confirmed_loose),
        "distinct_confirmed": len(distinct),
        "lag_n": len(lags),
        "lag_median_s": rounded(percentile(lags, 50)),
        "lag_p90_s": rounded(percentile(lags, 90)),
        "in_time": in_time,
        "in_time_share": rounded(in_time / len(lags)) if lags else None,
        "partial_jobs": sum(r.partial_jobs for r in replays),
        "partial_posts": sum(r.partial_posts for r in replays),
        "partial_audio_s": round(partial_audio_s, 1),
        "partial_x": round(partial_audio_s / replay_s, 3) if replay_s else None,
        "busy_share": round(busy_s / replay_s, 3) if replay_s else None,
        "final_waits": {
            "waited": len(waits),
            "median_s": rounded(percentile(waits, 50)),
            "p95_s": rounded(percentile(waits, 95)),
            "max_s": rounded(max(waits)) if waits else None,
            "behind_partial": waited_behind,
        },
    }


TABLE = [
    ("record", 18),
    ("hints", 5),
    ("conf", 5),
    ("unconf", 6),
    ("confLoose", 9),
    ("verses", 6),
    ("lagMed", 7),
    ("lagP90", 7),
    ("inTime", 6),
    ("pJobs", 6),
    ("pPosts", 6),
    ("pAudio", 8),
    ("pX", 6),
    ("busy", 6),
    ("waitMed", 8),
    ("waitP95", 8),
    ("waitMax", 8),
    ("behind", 6),
]
# Added with --truth timestamps (the columns above keep the final-based truth).
TS_TABLE = [
    ("confTs", 6),
    ("lagStartMed", 11),
    ("lagStartP90", 11),
    ("beforeEnd", 9),
    ("beforeEnd%", 10),
    ("versesRead", 10),
    ("hinted", 6),
]


def _row(name: str, m: dict[str, Any], ts: bool = False) -> list[str]:
    def cell(value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}"
        return str(value)

    waits = m["final_waits"]
    cells = [
        name,
        cell(m["hints"]),
        cell(m["confirmed"]),
        cell(m["unconfirmed"]),
        cell(m["confirmed_loose"]),
        cell(m["distinct_confirmed"]),
        cell(m["lag_median_s"]),
        cell(m["lag_p90_s"]),
        cell(m["in_time"]),
        cell(m["partial_jobs"]),
        cell(m["partial_posts"]),
        cell(m["partial_audio_s"]),
        cell(m["partial_x"]),
        cell(m["busy_share"]),
        cell(waits["median_s"]),
        cell(waits["p95_s"]),
        cell(waits["max_s"]),
        cell(waits["behind_partial"]),
    ]
    if ts:
        cells += [
            cell(m["confirmed_ts"]),
            cell(m["lag_start_median_s"]),
            cell(m["lag_start_p90_s"]),
            cell(m["before_end"]),
            cell(m["before_end_share"]),
            cell(m["verses_read"]),
            cell(m["hinted"]),
        ]
    return cells


def render_table(rows: list[tuple[str, dict[str, Any]]], ts: bool = False) -> str:
    columns = TABLE + TS_TABLE if ts else TABLE
    widths = [width for _, width in columns]

    def line(cells: list[str]) -> str:
        padded = [c.ljust(widths[0]) if i == 0 else c.rjust(widths[i]) for i, c in enumerate(cells)]
        return "  ".join(padded)

    header = [name for name, _ in columns]
    out = [line(header), "-" * (sum(widths) + 2 * (len(widths) - 1))]
    out += [line(_row(name, m, ts)) for name, m in rows]
    return "\n".join(out)


def hint_json(hint: Hint, ts: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "t_s": round(hint.t_s, 2),
        "source": hint.source,
        "utterance": hint.utterance,
        "follows": hint.follows,
        "verse": _ref_text(hint.book, hint.chapter, hint.verse, hint.verse_end),
        "shown": hint.shown,
        "confirmed": hint.confirmed,
        "confirmed_loose": hint.confirmed_loose,
        "lag_s": None if hint.lag_s is None else round(hint.lag_s, 2),
        "in_time": hint.in_time,
        "counted": hint.counted,
    }
    if ts:
        out.update(
            {
                "confirmed_ts": hint.ts_read is not None,
                "read_start_s": None if hint.ts_read is None else round(hint.ts_read.start_s, 2),
                "read_end_s": None if hint.ts_read is None else round(hint.ts_read.end_s, 2),
                "lag_start_s": None if hint.ts_lag_s is None else round(hint.ts_lag_s, 2),
                "in_time_ts": hint.ts_in_time,
                "counted_ts": hint.ts_counted,
            }
        )
    return out


def verse_read_json(read: VerseRead) -> dict[str, Any]:
    return {
        "verse": _ref_text(read.book, read.chapter, read.verse, None),
        "period_s": None if read.period_s is None else round(read.period_s, 2),
        "tokens": read.tokens,
        "matched": read.matched,
        "coverage": read.coverage,
        "start_s": round(read.start_s, 2),
        "end_s": round(read.end_s, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--corpus", default=str(DEFAULT_CORPUS), help="lines: <manifest> <record> [START:END]"
    )
    ap.add_argument(
        "--records", default=None, help="comma-separated subset of the corpus's record ids"
    )
    ap.add_argument("--audio-dir", default=None, help="directory containing audio (or env var)")
    ap.add_argument("--cache-dir", default="results/.bench-cache", help="feed cache (finals)")
    ap.add_argument(
        "--partial-cache-dir",
        default=None,
        help="partial transcripts (default: <cache-dir>/follow-along-partials)",
    )
    ap.add_argument(
        "--bible-db",
        default=str(REPO_ROOT / "bible_data" / "biblelive.sqlite"),
        help="Bible DB the server validates hints against (pass the main checkout's path)",
    )
    ap.add_argument(
        "--verse-text-db",
        default=str(REPO_ROOT / "bible_data" / "bible_ltb.sqlite"),
        help="Lithuanian verse-text DB the follow-along matching scores against",
    )
    ap.add_argument(
        "--context-ttl-s", type=float, default=600.0, help="parser context TTL of the cached finals"
    )
    ap.add_argument("--device", default=None, help="STT device (cpu, cuda); part of the cache key")
    ap.add_argument("--model", default=None, help="Whisper model size; part of the cache key")
    # --stt, --kmynas-model, --cpu-threads and segmentation are shared with the live CLI, as
    # are the partial settings (--partial-transcripts, --partial-mode, --partial-interval-s,
    # --partial-min-s); run_benchmark.py takes the first group only.
    add_tuning_arguments(ap)
    add_partial_arguments(ap)
    ap.add_argument(
        "--partials",
        choices=["on", "off"],
        default=None,
        help="shorthand for --partial-transcripts on|off (the baseline is off)",
    )
    ap.add_argument(
        "--truth",
        choices=["final", "timestamps"],
        default="final",
        help="what confirms a hint: the final utterance that read its verse (default), or the "
        "verse's own timed reading from Kmynas word timestamps (adds columns)",
    )
    ap.add_argument(
        "--verse-read-bar",
        type=float,
        default=0.5,
        help="share of a verse's words that must be aligned for it to count as read (timestamps)",
    )
    ap.add_argument(
        "--dump-verses",
        type=int,
        default=0,
        help="print N random verse readings with their words and times (timestamps)",
    )
    ap.add_argument("--dump-seed", type=int, default=1, help="seed of the --dump-verses sample")
    ap.add_argument("--final-rtf", type=float, default=0.21, help="worker seconds per audio second")
    ap.add_argument(
        "--partial-rtf", type=float, default=0.21, help="worker seconds per audio second"
    )
    ap.add_argument("--display-delay-s", type=float, default=3.0)
    ap.add_argument("--operator-delay-s", type=float, default=1.5)
    ap.add_argument("--chain", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--min-score", type=float, default=0.5)
    ap.add_argument("--next-min-score", type=float, default=None)
    ap.add_argument("--pick", choices=["best", "furthest"], default=HintPolicy.pick)
    ap.add_argument("--lookahead", type=int, default=8)
    # The verse-start rule (HintPolicy.start_*; see core/server/follow_along.verse_started).
    ap.add_argument(
        "--start-match", choices=["on", "off"], default="on" if HintPolicy.start_match else "off"
    )
    ap.add_argument("--start-prefix", type=int, default=HintPolicy.start_prefix)
    ap.add_argument("--start-min-tokens", type=int, default=HintPolicy.start_min_tokens)
    ap.add_argument("--start-min-idf", type=float, default=HintPolicy.start_min_idf)
    ap.add_argument("--start-anchor", type=int, default=HintPolicy.start_anchor)
    ap.add_argument("--start-prev-min", type=float, default=HintPolicy.start_prev_min)
    ap.add_argument("--arm-after-display-s", type=float, default=60.0)
    ap.add_argument("--arm-hold-s", type=float, default=30.0)
    ap.add_argument(
        "--regress-hold-s",
        type=float,
        default=REGRESS_HOLD_S,
        help="no verse below the latest hint is offered for this long after it",
    )
    ap.add_argument(
        "--out", required=True, help="JSON output; an existing file is never overwritten"
    )
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        print(
            f"error: {out} exists; choose a new name (results are never overwritten)",
            file=sys.stderr,
        )
        return 2
    for label, path in (("--bible-db", args.bible_db), ("--verse-text-db", args.verse_text_db)):
        if not Path(path).is_file():
            print(
                f"error: {label} {path} not found (pass the main checkout's bible_data path)",
                file=sys.stderr,
            )
            return 2

    config = AudioPipelineConfig(control_enabled=False)  # no language-control server
    if args.device:
        config.whisper_device = args.device
    if args.model:
        config.whisper_model = args.model
    apply_tuning_arguments(config, args)
    if args.partials is not None:
        config.partial_transcripts = args.partials == "on"
    if config.partial_transcripts and config.stt_engine not in ("kmynas", "kmynas+canary"):
        print(
            "error: --partials on needs --stt kmynas or kmynas+canary (partials run the Kmynas model)",
            file=sys.stderr,
        )
        return 2
    if args.truth == "timestamps" and not config.kmynas_model:
        print(
            "error: --truth timestamps needs --kmynas-model (word timestamps come from Kmynas)",
            file=sys.stderr,
        )
        return 2
    if args.dump_verses and args.truth != "timestamps":
        print("error: --dump-verses needs --truth timestamps", file=sys.stderr)
        return 2
    policy = HintPolicy(
        min_score=args.min_score,
        next_min_score=args.next_min_score,
        pick=args.pick,
        lookahead=args.lookahead,
        start_match=args.start_match == "on",
        start_prefix=args.start_prefix,
        start_min_tokens=args.start_min_tokens,
        start_min_idf=args.start_min_idf,
        start_anchor=args.start_anchor,
        start_prev_min=args.start_prev_min,
    )

    corpus = load_corpus(Path(args.corpus))
    if args.records:
        wanted = [r for r in args.records.split(",") if r]
        known = {entry.record_id for entry in corpus}
        missing = [r for r in wanted if r not in known]
        if missing:
            print(f"error: not in {args.corpus}: {', '.join(missing)}", file=sys.stderr)
            return 2
        corpus = [entry for entry in corpus if entry.record_id in wanted]
    audio_dir = Path(args.audio_dir or os.environ.get("BIBLELIVE_BENCH_AUDIO_DIR", ""))
    if not audio_dir.is_dir():
        print(
            "error: --audio-dir (or BIBLELIVE_BENCH_AUDIO_DIR) must name the recordings",
            file=sys.stderr,
        )
        return 2
    cache_dir = Path(args.cache_dir)
    partial_cache = (
        Path(args.partial_cache_dir)
        if args.partial_cache_dir
        else cache_dir / "follow-along-partials"
    )
    timer = WordTimer(cache_dir, config) if args.truth == "timestamps" else None

    index = VerseTextIndex.from_sqlite(args.verse_text_db)
    bible = BibleLookup(args.bible_db, readonly=True)

    results: list[ReplayResult] = []
    for entry in corpus:
        manifest = load_manifest(entry.manifest)
        rec_dict = next((r for r in manifest["recordings"] if r.get("id") == entry.record_id), None)
        if rec_dict is None:
            raise SystemExit(f"{entry.record_id} is not in {entry.manifest}")
        rec = Recording(**{k: rec_dict.get(k) for k in Recording.__dataclass_fields__})
        gold = load_gold(entry.manifest.parent / rec.gold_file, rec.id) if rec.gold_file else []
        audio_path = audio_dir / rec.file
        if not audio_path.is_file():
            raise SystemExit(f"{rec.id}: audio not found at {audio_path}")
        audio_sha = verified_audio_sha(rec, audio_path)
        key = offline_feed_key(rec, audio_sha, "offline", config, args.context_ttl_s, entry.t_range)
        cpath = feed_cache_path(cache_dir, key)
        cached = read_feed_cache(cpath)
        if cached is None or cached.get("key") != key:
            raise SystemExit(
                f"{rec.id}: no matching finals cache at {cpath}. Finals are never transcribed "
                "here: run the detection benchmark (--mode offline, the same --stt, --device, "
                "--model, --context-ttl-s and t-range) first."
            )
        pcm = load_pcm(audio_path, audio_dir, cache_dir)
        first, last = frame_bounds(len(pcm) // BYTES_PER_FRAME, entry.t_range)

        vad = make_vad(config)
        partial_tx = (
            PartialTexts(partial_cache, rec, audio_sha, config)
            if config.partial_transcripts
            else None
        )
        finals = CachedFinals(cached)
        replay = FollowAlongReplay(
            record_id=rec.id,
            language=rec.language,
            gold=gold,
            feed=finals,
            index=index,
            bible=bible,
            server=FollowAlong(
                policy, args.arm_after_display_s, args.arm_hold_s, args.regress_hold_s
            ),
            config=config,
            vad_is_speech=vad.is_speech,
            transcribe_partial=partial_tx,
            final_rtf=args.final_rtf,
            partial_rtf=args.partial_rtf,
            display_delay_s=args.display_delay_s,
            operator_delay_s=args.operator_delay_s,
            chain=args.chain,
        )
        t0 = time.time()
        result = replay.run(pcm, first, last)
        finals.finished()
        result.pairs = confirm_hints(result.hints, result.finals, index, result.utterance_ends)
        if timer is not None:
            periods = display_periods(gold, args.display_delay_s)
            timed_finals = CachedFinals(cached)
            spoken = spoken_by_period(
                pcm,
                first,
                last,
                config,
                make_vad(config).is_speech,
                timed_finals,
                periods,
                functools.partial(timer, rec.id, rec.language),
            )
            timed_finals.finished()
            result.verse_reads = read_periods(
                periods, spoken, args.verse_text_db, args.verse_read_bar
            )
            counted_ts = confirm_by_timestamps(result.hints, result.verse_reads)
            print(
                f"       verses: {len(periods)} display periods, {sum(len(s) for s in spoken)} "
                f"timed words, {len(result.verse_reads)} verses read, {len(counted_ts)} counted "
                f"hints; words {timer.transcribed} transcribed, {timer.from_cache} cached",
                flush=True,
            )
        results.append(result)
        m = metrics([result])
        print(
            f"[done] {rec.id}: replay {result.replay_s:.0f}s, {len(result.finals)} finals, "
            f"{m['hints']} hints ({m['confirmed']} confirmed), {m['partial_jobs']} partials, "
            f"{time.time() - t0:.0f}s wall",
            flush=True,
        )
        if partial_tx is not None:
            print(
                f"       partials: {partial_tx.transcribed} transcribed, "
                f"{partial_tx.from_cache} from cache",
                flush=True,
            )

    ts = args.truth == "timestamps"
    rows = [(r.record_id, summarize([r], args.truth)) for r in results]
    totals = summarize(results, args.truth)
    print()
    print(render_table(rows + [("TOTAL", totals)], ts=ts))
    samples = sample_reads(results, args.dump_verses, args.dump_seed) if args.dump_verses else []
    if samples:
        print()
        print(f"verse readings, random sample (seed {args.dump_seed}):")
        print(render_samples(samples))
    payload = {
        "benchmark": "follow_along",
        "version": 1,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            **{k: v for k, v in vars(args).items() if k != "out"},
            "policy": {
                "min_score": policy.min_score,
                "next_min_score": policy.next_min_score,
                "pick": policy.pick,
                "lookahead": policy.lookahead,
                "start_match": policy.start_match,
                "start_prefix": policy.start_prefix,
                "start_min_tokens": policy.start_min_tokens,
                "start_min_idf": policy.start_min_idf,
                "start_anchor": policy.start_anchor,
                "start_prev_min": policy.start_prev_min,
            },
            "stt": stt_cache_identity(config),
            # What the run used after the flags were applied (the flags above are as typed).
            "effective": {
                "partial_transcripts": config.partial_transcripts,
                "partial_mode": config.partial_mode,
                "partial_interval_s": config.partial_interval_s,
                "partial_min_s": config.partial_min_s,
                "partial_frames": {
                    "interval": PartialScheduler.from_config(config).interval_frames,
                    "min": PartialScheduler.from_config(config).min_frames,
                },
                "stt_engine": config.stt_engine,
                "whisper_device": config.whisper_device,
                "whisper_model": config.whisper_model,
            },
        },
        "totals": totals,
        "recordings": [
            {
                "record_id": r.record_id,
                **summarize([r], args.truth),
                "hints": [hint_json(h, ts=ts) for h in r.hints],
                **({"verse_reads": [verse_read_json(v) for v in r.verse_reads]} if ts else {}),
            }
            for r in results
        ],
        **(
            {
                "verse_samples": [
                    {
                        "record_id": rid,
                        **verse_read_json(v),
                        "text": v.text,
                        "heard": [
                            {
                                "spoken": spoken,
                                "stem": stem,
                                "start_s": round(start, 2),
                                "end_s": round(end, 2),
                            }
                            for stem, spoken, start, end in v.aligned
                        ],
                    }
                    for rid, v in samples
                ]
            }
            if samples
            else {}
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as f:  # 'x': never overwrite
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    print(f"results written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
