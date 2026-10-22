"""Partial transcripts of the utterance still being spoken, for follow-along.

A final utterance can reach the server 10 to 14 s after the verse was first
read aloud, so the server's follow-along hint comes late. While the server says
follow-along is armed, the VAD thread snapshots the open utterance every
``partial_interval_s`` of its audio into a one-slot mailbox (newest wins), and
the transcription thread transcribes a snapshot only when no final is waiting.
Partials only feed ``/api/transcript``: segmentation and finals are unchanged,
so reference detection is unchanged too.

Pure bookkeeping, shared by the live pipeline and the benchmark
(``benchmarks/follow_along_bench.py``) so the benchmark schedules exactly what
runs live. No threads, except the mailbox's lock.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .config import AudioPipelineConfig
from .segmenter import FRAME_DURATION_MS, Segmenter, SegmentEvent

# The shortest chunk worth transcribing: 0.5 s of 16 kHz 16-bit mono, the length
# under which the GGUF speech models skip an utterance (stt_gguf.py).
MIN_CHUNK_BYTES = 16000 * 2 // 2


def _frames(seconds: float) -> int:
    """Seconds as whole VAD frames, as Segmenter.from_config rounds them."""
    return round(seconds * 1000 / FRAME_DURATION_MS)


class PartialScheduler:
    """Says when the open utterance is due a partial transcript.

    Called by the VAD thread after each ``Segmenter.push``. A partial is due at
    each multiple of ``interval_frames`` of utterance time (counted from its
    start) that is at least ``min_frames`` long. It is due at most once per
    multiple, and a long jump skips the multiples it passed rather than
    bursting. The caller decides whether partials are armed; this tracks timing
    only, so the schedule advances whether or not a partial is sent.
    """

    def __init__(self, interval_frames: int, min_frames: int):
        if interval_frames < 1:
            raise ValueError(f"interval_frames must be >= 1, got {interval_frames}")
        if min_frames < 0:
            raise ValueError(f"min_frames must be >= 0, got {min_frames}")
        self.interval_frames = interval_frames
        self.min_frames = min_frames
        self._next: int | None = None  # utterance frames at which the next partial is due

    @classmethod
    def from_config(cls, config: AudioPipelineConfig) -> PartialScheduler:
        """Build from the config's seconds, rounded to whole VAD frames."""
        return cls(_frames(config.partial_interval_s), _frames(config.partial_min_s))

    def due(self, segmenter: Segmenter, event: SegmentEvent | None) -> bool:
        """True when a partial is due after this frame; ``event`` is what the frame did."""
        if event is not None and event.kind != "start":
            self._next = None  # the utterance closed, so its partials are over
            return False
        if not segmenter.in_utterance:
            self._next = None  # no utterance (e.g. reset without an event)
            return False
        if event is not None or self._next is None:  # a new utterance begins
            first = max(self.min_frames, 1)
            self._next = -(-first // self.interval_frames) * self.interval_frames
        frames = segmenter.utterance_frames
        if frames < self._next:
            return False
        self._next = (frames // self.interval_frames + 1) * self.interval_frames
        return True


@dataclass(frozen=True)
class PartialSnapshot:
    """The open utterance's audio so far, as the VAD thread holds it."""

    utterance: int  # the caller's sequence number, bumped at each utterance start
    language: str
    frames: tuple[bytes, ...]


class PartialMailbox:
    """One waiting snapshot, passed from the VAD thread (put) to the worker (take).

    Newest wins: a snapshot still waiting is replaced, so the worker transcribes
    the latest audio and never a backlog of stale snapshots.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting: PartialSnapshot | None = None

    def put(self, snapshot: PartialSnapshot) -> bool:
        """Store ``snapshot``, replacing any waiting one; True when one was replaced."""
        with self._lock:
            replaced = self._waiting is not None
            self._waiting = snapshot
            return replaced

    def take(self) -> PartialSnapshot | None:
        """Remove and return the waiting snapshot, or None."""
        with self._lock:
            snapshot, self._waiting = self._waiting, None
            return snapshot

    def discard(self, utterance: int | None = None) -> None:
        """Drop the waiting snapshot; with ``utterance``, only if it belongs to that one.

        Called when an utterance closes: its final covers the audio from then on.
        """
        with self._lock:
            if self._waiting is None:
                return
            if utterance is None or self._waiting.utterance == utterance:
                self._waiting = None


class PartialTranscript:
    """The transcription thread's side: what audio to transcribe per snapshot, and what to post.

    ``grow``: each partial transcribes the whole utterance so far, and its text
    replaces the last one (the best text, at a cost that grows with the square of
    the utterance length).
    ``chunk``: each partial transcribes only the audio since the previous partial,
    and the chunk texts are joined (about 1x the audio, but a word split between
    two chunks can come out mangled).

    A new utterance number resets the state. Call ``add`` after each
    ``audio_for`` that returned audio, with the text it produced (None when
    nothing was heard or it was not confident).
    """

    def __init__(self, mode: str):
        if mode not in ("grow", "chunk"):
            raise ValueError(f"partial mode must be 'grow' or 'chunk', got {mode!r}")
        self.mode = mode
        self._utterance: int | None = None
        self._done = 0  # frames of the utterance already transcribed (chunk mode)
        self._chunks: list[str] = []  # chunk texts so far (chunk mode)
        self._last: str | None = None  # last text returned for this utterance

    def _sync(self, snapshot: PartialSnapshot) -> None:
        if snapshot.utterance != self._utterance:
            self._utterance = snapshot.utterance
            self._done = 0
            self._chunks = []
            self._last = None

    def audio_for(self, snapshot: PartialSnapshot) -> bytes | None:
        """The audio to transcribe for ``snapshot``; None when there is nothing new.

        In chunk mode, new audio shorter than MIN_CHUNK_BYTES also gives None and stays
        pending for the next snapshot: the speech models skip such short audio, and
        marking it done would lose its words from every later partial.
        """
        self._sync(snapshot)
        frames = snapshot.frames[self._done :] if self.mode == "chunk" else snapshot.frames
        audio = b"".join(frames)
        if not audio or (self.mode == "chunk" and len(audio) < MIN_CHUNK_BYTES):
            return None
        return audio

    def add(self, snapshot: PartialSnapshot, text: str | None) -> str | None:
        """Record the text of ``snapshot``'s audio; return the text to post, or None."""
        self._sync(snapshot)
        text = (text or "").strip()
        if self.mode == "grow":
            joined = text
        else:
            self._done = len(snapshot.frames)
            if text:
                self._chunks.append(text)
            joined = " ".join(self._chunks)
        if not joined or joined == self._last:
            return None
        self._last = joined
        return joined
