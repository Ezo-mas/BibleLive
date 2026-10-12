"""Utterance segmentation: turn a stream of VAD frames into utterances.

Shared by the live pipeline (``pipeline._vad_loop``) and the benchmark
(``benchmarks/run_benchmark.py``), so the benchmark measures exactly the
segmentation that runs live. Pure bookkeeping: no threads, queues or clocks.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

FRAME_DURATION_MS = 30  # WebRTC VAD frame size (10/20/30ms)
MAX_SILENT_FRAMES = 50  # ~1.5 seconds of silence to end utterance
MIN_SPEECH_FRAMES = 10  # Minimum frames to consider as speech (~300ms)


@dataclass(frozen=True)
class SegmentEvent:
    """What a frame did to the current utterance.

    ``start``: the first voiced frame of a new utterance.
    ``end``: an utterance closed with at least ``min_speech_frames`` of speech;
    ``frames`` holds its voiced frames.
    ``blip``: an utterance closed too short to transcribe; ``frames`` holds it
    for logging only.
    """

    kind: Literal["start", "end", "blip"]
    frames: list[bytes] = field(default_factory=list)
    trailing_silent_frames: int = 0


class Segmenter:
    """Assemble voiced frames into utterances, closed by silence.

    Only voiced frames are kept: silent frames inside an utterance count
    toward the end-of-speech wait but are not part of the audio.
    """

    def __init__(
        self,
        is_speech: Callable[[bytes], bool],
        max_silent_frames: int = MAX_SILENT_FRAMES,
        min_speech_frames: int = MIN_SPEECH_FRAMES,
    ):
        self._is_speech = is_speech
        self.max_silent_frames = max_silent_frames
        self.min_speech_frames = min_speech_frames
        self.speech_frames: list[bytes] = []
        self.silent_frames = 0

    @property
    def in_utterance(self) -> bool:
        return bool(self.speech_frames)

    def push(self, frame: bytes) -> SegmentEvent | None:
        """Feed one VAD frame; return the event it caused, if any."""
        if self._is_speech(frame):
            started = not self.speech_frames
            self.silent_frames = 0
            self.speech_frames.append(frame)
            return SegmentEvent("start") if started else None

        if not self.speech_frames:
            return None
        self.silent_frames += 1
        if self.silent_frames > self.max_silent_frames:
            return self._close()
        return None

    def flush(self) -> SegmentEvent | None:
        """Close an open utterance at end of input (no silence wait)."""
        if not self.speech_frames:
            return None
        return self._close()

    def reset(self) -> int:
        """Discard any partial utterance; return how many frames were dropped."""
        dropped = len(self.speech_frames)
        self.speech_frames = []
        self.silent_frames = 0
        return dropped

    def _close(self) -> SegmentEvent:
        frames, silent = self.speech_frames, self.silent_frames
        self.reset()
        kind: Literal["end", "blip"] = "end" if len(frames) >= self.min_speech_frames else "blip"
        return SegmentEvent(kind, frames, silent)
