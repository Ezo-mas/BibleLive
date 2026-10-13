"""Utterance segmentation: turn a stream of VAD frames into utterances.

Shared by the live pipeline (``pipeline._vad_loop``) and the benchmark
(``benchmarks/run_benchmark.py``), so the benchmark measures exactly the
segmentation that runs live. Pure bookkeeping: no threads, queues or clocks.
"""

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

    ``start``: the first voiced frame of a new utterance.
    ``end``: an utterance closed with at least ``min_speech_frames`` of speech;
    ``frames`` holds its voiced frames.
    ``blip``: an utterance closed too short to transcribe; ``frames`` holds it
    for logging only.
    ``reason`` says what closed it, ``trailing_silent_frames`` how much silence.
    """

    kind: Literal["start", "end", "blip"]
    frames: list[bytes] = field(default_factory=list)
    trailing_silent_frames: int = 0
    reason: EndReason | None = None


class Segmenter:
    """Assemble voiced frames into utterances, closed by silence or length.

    Only voiced frames are kept: silent frames inside an utterance count
    toward the end-of-speech wait and the length limits but are not part of
    the audio.
    """

    def __init__(
        self,
        is_speech: Callable[[bytes], bool],
        max_silent_frames: int = MAX_SILENT_FRAMES,
        min_speech_frames: int = MIN_SPEECH_FRAMES,
        soft_utterance_frames: int = SOFT_UTTERANCE_FRAMES,
        max_utterance_frames: int = MAX_UTTERANCE_FRAMES,
    ):
        self._is_speech = is_speech
        self.max_silent_frames = max_silent_frames
        self.min_speech_frames = min_speech_frames
        self.soft_utterance_frames = soft_utterance_frames
        self.max_utterance_frames = max_utterance_frames
        self.speech_frames: list[bytes] = []
        self.silent_frames = 0
        self.utterance_frames = 0  # voiced + silent frames since the start

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
        )

    @property
    def in_utterance(self) -> bool:
        return bool(self.speech_frames)

    def push(self, frame: bytes) -> SegmentEvent | None:
        """Feed one VAD frame; return the event it caused, if any."""
        if self._is_speech(frame):
            started = not self.speech_frames
            self.silent_frames = 0
            self.speech_frames.append(frame)
            self.utterance_frames += 1
            if self.utterance_frames >= self.max_utterance_frames:
                return self._close("cap")
            return SegmentEvent("start") if started else None

        if not self.speech_frames:
            return None
        self.silent_frames += 1
        self.utterance_frames += 1
        if self.silent_frames > self.max_silent_frames:
            return self._close("silence")
        if self.utterance_frames >= self.soft_utterance_frames:
            return self._close("pause")
        return None

    def flush(self) -> SegmentEvent | None:
        """Close an open utterance at end of input (no silence wait)."""
        if not self.speech_frames:
            return None
        return self._close("flush")

    def reset(self) -> int:
        """Discard any partial utterance; return how many frames were dropped."""
        dropped = len(self.speech_frames)
        self.speech_frames = []
        self.silent_frames = 0
        self.utterance_frames = 0
        return dropped

    def _close(self, reason: EndReason) -> SegmentEvent:
        frames, silent = self.speech_frames, self.silent_frames
        self.reset()
        kind: Literal["end", "blip"] = "end" if len(frames) >= self.min_speech_frames else "blip"
        return SegmentEvent(kind, frames, silent, reason)
