"""Utterance segmentation: turn a stream of VAD frames into utterances.

Shared by the live pipeline (``pipeline._vad_loop``) and the benchmark
(``benchmarks/run_benchmark.py``), so the benchmark measures exactly the
segmentation that runs live. Pure bookkeeping: no threads, queues or clocks.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .config import AudioPipelineConfig

FRAME_DURATION_MS = 30  # WebRTC VAD frame size (10/20/30ms)
MAX_SILENT_FRAMES = 50  # ~1.5 seconds of silence to end utterance
MIN_SPEECH_FRAMES = 10  # Minimum frames to consider as speech (~300ms)

# A service mixed from ungated mics and instruments rarely gives the VAD 1.5s
# of silence, so without a limit one utterance can run for many minutes and
# its references reach the operator only when it finally closes. Past the
# soft limit the utterance ends at the next unvoiced frame (a gap between
# words); at the hard cap it ends regardless. No audio overlap: the
# StreamingParser's token carry joins a reference cut in two, while re-sent
# overlap audio would duplicate references outside the sender's dedup window.
SOFT_UTTERANCE_FRAMES = 267  # ~8 seconds, then cut at the next pause
MAX_UTTERANCE_FRAMES = 400  # ~12 seconds, hard cap

EndReason = Literal["silence", "pause", "cap", "flush"]


@dataclass(frozen=True)
class SegmentEvent:
    """What a frame did to the current utterance.

    ``start``: a new utterance began (the start trigger was met);
    ``onset_frames`` counts the frames from its first voiced frame up to and
    including this one, so the caller can date the onset.
    ``end``: an utterance closed with at least ``min_speech_frames`` of speech;
    ``frames`` holds its audio: the voiced frames, plus the pre-roll, the
    in-utterance pauses and the post-roll when those are on.
    ``blip``: an utterance closed too short to transcribe; ``frames`` holds it
    for logging only.
    ``reason`` says what closed it, ``trailing_silent_frames`` how much silence.
    """

    kind: Literal["start", "end", "blip"]
    frames: list[bytes] = field(default_factory=list)
    trailing_silent_frames: int = 0
    reason: EndReason | None = None
    onset_frames: int = 1


class Segmenter:
    """Assemble voiced frames into utterances, closed by silence or length.

    By default only voiced frames are kept: silent frames inside an utterance
    count toward the end-of-speech wait and the length limits but are not part
    of the audio. Options (all off by default):

    - ``pre_roll_frames``: frames from just before the first voiced frame
      are kept too, so a soft first sound the VAD missed still reaches Whisper.
    - ``keep_pauses``: frames the VAD calls silent between voiced ones stay in
      the audio (soft consonants, short pauses).
    - ``post_roll_frames``: up to this many of the silent frames after the
      last voiced one end the utterance's audio, so a soft last sound the VAD
      missed is not cut; the rest of the trailing silence is dropped.
    - ``start_voiced_frames`` of the last ``start_window_frames`` must be
      voiced to start an utterance (1 of 1: the first voiced frame). The
      utterance then begins at the window's first voiced frame (preceded by
      the pre-roll), not at the frame that met the trigger.
    """

    def __init__(
        self,
        is_speech: Callable[[bytes], bool],
        max_silent_frames: int = MAX_SILENT_FRAMES,
        min_speech_frames: int = MIN_SPEECH_FRAMES,
        soft_utterance_frames: int = SOFT_UTTERANCE_FRAMES,
        max_utterance_frames: int = MAX_UTTERANCE_FRAMES,
        pre_roll_frames: int = 0,
        keep_pauses: bool = False,
        start_voiced_frames: int = 1,
        start_window_frames: int = 1,
        post_roll_frames: int = 0,
    ):
        if not 1 <= start_voiced_frames <= start_window_frames:
            raise ValueError(
                f"need 1 <= start_voiced_frames ({start_voiced_frames}) "
                f"<= start_window_frames ({start_window_frames})"
            )
        if pre_roll_frames < 0:
            raise ValueError(f"pre_roll_frames must be >= 0, got {pre_roll_frames}")
        if post_roll_frames < 0:
            raise ValueError(f"post_roll_frames must be >= 0, got {post_roll_frames}")
        self._is_speech = is_speech
        self.max_silent_frames = max_silent_frames
        self.min_speech_frames = min_speech_frames
        self.soft_utterance_frames = soft_utterance_frames
        self.max_utterance_frames = max_utterance_frames
        self.keep_pauses = keep_pauses
        self.start_voiced_frames = start_voiced_frames
        self.start_window_frames = start_window_frames
        self.pre_roll_frames = pre_roll_frames
        self.post_roll_frames = post_roll_frames
        self.speech_frames: list[bytes] = []  # the utterance's audio so far
        self.voiced_frames = 0  # voiced frames in it (the blip test)
        # Silent frames since the last voiced one: all of them with keep_pauses,
        # else the first post_roll_frames.
        self._pause: list[bytes] = []
        self.silent_frames = 0
        self.utterance_frames = 0  # voiced + silent frames since the start
        # Before an utterance: the recent frames (pre-roll + start window).
        self._recent: deque[tuple[bytes, bool]] = deque(
            maxlen=pre_roll_frames + start_window_frames
        )

    @classmethod
    def from_config(cls, is_speech: Callable[[bytes], bool], config: AudioPipelineConfig):
        """Build from the config's durations, rounded to whole VAD frames."""

        def frames(seconds: float) -> int:
            return round(seconds * 1000 / FRAME_DURATION_MS)

        soft, cap = frames(config.soft_utterance_s), frames(config.max_utterance_s)
        if not 0 < soft <= cap:
            raise ValueError(
                f"need 0 < soft_utterance_s ({config.soft_utterance_s}) "
                f"<= max_utterance_s ({config.max_utterance_s})"
            )
        return cls(
            is_speech,
            max_silent_frames=frames(config.end_of_speech_s),
            min_speech_frames=frames(config.min_speech_s),
            soft_utterance_frames=soft,
            max_utterance_frames=cap,
            pre_roll_frames=frames(config.pre_roll_s),
            post_roll_frames=frames(config.post_roll_s),
            keep_pauses=config.keep_pauses,
            start_voiced_frames=config.start_voiced_frames,
            start_window_frames=config.start_window_frames,
        )

    @property
    def in_utterance(self) -> bool:
        return bool(self.speech_frames)

    def push(self, frame: bytes) -> SegmentEvent | None:
        """Feed one VAD frame; return the event it caused, if any."""
        voiced = self._is_speech(frame)
        if not self.speech_frames:
            return self._maybe_start(frame, voiced)

        if voiced:
            self.silent_frames = 0
            if self.keep_pauses:  # a pause between words
                self.speech_frames.extend(self._pause)
            self._pause = []
            self.speech_frames.append(frame)
            self.voiced_frames += 1
            self.utterance_frames += 1
            if self.utterance_frames >= self.max_utterance_frames:
                return self._close("cap")
            return None

        self.silent_frames += 1
        self.utterance_frames += 1
        if self.keep_pauses or len(self._pause) < self.post_roll_frames:
            self._pause.append(frame)
        if self.silent_frames > self.max_silent_frames:
            return self._close("silence")
        if self.utterance_frames >= self.soft_utterance_frames:
            return self._close("pause")
        return None

    def _maybe_start(self, frame: bytes, voiced: bool) -> SegmentEvent | None:
        """Outside an utterance: start one once the start window is voiced enough."""
        self._recent.append((frame, voiced))
        recent = list(self._recent)
        window = recent[-self.start_window_frames :]
        if not voiced or sum(v for _, v in window) < self.start_voiced_frames:
            return None
        self._recent.clear()
        # The speech began at the window's first voiced frame. The pre-roll is
        # the audio right before it, kept whole and contiguous; from the onset
        # on, silent frames are kept only with keep_pauses, like any pause.
        onset = len(recent) - len(window)
        while not recent[onset][1]:
            onset += 1
        first = max(0, onset - self.pre_roll_frames)
        self.speech_frames = [f for f, _ in recent[first:onset]] + [
            f for f, v in recent[onset:] if v or self.keep_pauses
        ]
        self.voiced_frames = sum(v for _, v in recent[onset:])
        self.utterance_frames = len(recent) - first
        self.silent_frames = 0
        if self.utterance_frames >= self.max_utterance_frames:
            return self._close("cap")
        return SegmentEvent("start", onset_frames=len(recent) - onset)

    def flush(self) -> SegmentEvent | None:
        """Close an open utterance at end of input (no silence wait)."""
        if not self.speech_frames:
            # The stream ended (or capture restarts): its pre-roll must not
            # lead the first utterance of whatever comes next.
            self._recent.clear()
            return None
        return self._close("flush")

    def reset(self) -> int:
        """Discard any partial utterance; return how many frames were dropped."""
        dropped = len(self.speech_frames)
        self.speech_frames = []
        self.voiced_frames = 0
        self._pause = []
        self._recent.clear()
        self.silent_frames = 0
        self.utterance_frames = 0
        return dropped

    def _close(self, reason: EndReason) -> SegmentEvent:
        frames = self.speech_frames + self._pause[: self.post_roll_frames]
        silent, voiced = self.silent_frames, self.voiced_frames
        self.reset()  # drops the rest of the trailing pause and the pre-roll history
        kind: Literal["end", "blip"] = "end" if voiced >= self.min_speech_frames else "blip"
        return SegmentEvent(kind, frames, silent, reason)
