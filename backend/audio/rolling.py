"""Rolling-window transcription: an alternative to VAD utterances (prototype).

Every ``hop_s`` the last ``window_s`` of audio is transcribed with word
timestamps. Only the words that *start* in the not-yet-committed stretch are
emitted, and that stretch ends ``lookahead_s`` before the newest audio so a
word at the window's edge is decoded with context on both sides first. Each
moment of audio is therefore committed exactly once — overlapping windows give
Whisper context without producing duplicate references.

Pure bookkeeping (no threads, no model): callers transcribe ``window()`` and
pass the words back to ``commit()``. Times are seconds of audio since the
stream started.
"""

from dataclasses import dataclass

import numpy as np

from .stt import TimedWord

SAMPLE_RATE = 16000
WINDOW_S = 15.0
HOP_S = 5.0
LOOKAHEAD_S = 2.0


@dataclass(frozen=True)
class Commit:
    """The words committed by one hop.

    ``start_s``/``end_s`` is the audio stretch committed (each moment once).
    ``spoken_s`` is when the first committed word was said (None if no words).
    ``low_confidence_reason`` is set if any committed word came from a shaky
    Whisper segment.
    """

    start_s: float
    end_s: float
    text: str
    spoken_s: float | None
    low_confidence_reason: str | None


class RollingWindow:
    """Ring buffer of recent audio plus the commit boundary."""

    def __init__(
        self,
        window_s: float = WINDOW_S,
        hop_s: float = HOP_S,
        lookahead_s: float = LOOKAHEAD_S,
        sample_rate: int = SAMPLE_RATE,
    ):
        # The stretch committed each hop, [now - hop - lookahead, now - lookahead),
        # must still be inside the window that was just transcribed.
        if hop_s <= 0 or lookahead_s < 0 or hop_s + lookahead_s > window_s:
            raise ValueError("need hop_s > 0, lookahead_s >= 0, hop_s + lookahead_s <= window_s")
        self.window_s = window_s
        self.hop_s = hop_s
        self.lookahead_s = lookahead_s
        self.sample_rate = sample_rate
        self._buffer = np.zeros(0, dtype=np.float32)
        self._buffer_start = 0  # absolute sample index of _buffer[0]
        self._total = 0  # samples received
        self._last_window_end = 0  # absolute sample index of the last window's end
        self.committed_until_s = 0.0

    @property
    def now_s(self) -> float:
        return self._total / self.sample_rate

    def push(self, samples: np.ndarray) -> None:
        """Append float32 samples, dropping audio older than one window."""
        self._buffer = np.concatenate([self._buffer, samples.astype(np.float32, copy=False)])
        self._total += len(samples)
        keep = int(self.window_s * self.sample_rate)
        if len(self._buffer) > keep:
            drop = len(self._buffer) - keep
            self._buffer = self._buffer[drop:]
            self._buffer_start += drop

    @property
    def due(self) -> bool:
        """A hop's worth of new audio has arrived since the last window."""
        return self._total - self._last_window_end >= self.hop_s * self.sample_rate

    def window(self) -> tuple[np.ndarray, float]:
        """The audio to transcribe now and its absolute start time."""
        self._last_window_end = self._total
        return self._buffer.copy(), self._buffer_start / self.sample_rate

    def commit(self, words: list[TimedWord], window_start_s: float, final: bool = False) -> Commit:
        """Take the words of the latest window that start in the uncommitted stretch.

        ``final`` commits up to the end of the audio (end of stream).
        """
        lo = self.committed_until_s
        hi = self.now_s if final else max(lo, self.now_s - self.lookahead_s)
        picked = [w for w in words if lo <= window_start_s + w.start < hi]
        self.committed_until_s = hi
        text = "".join(w.text for w in picked).strip()
        reason = next((w.low_confidence_reason for w in picked if w.low_confidence_reason), None)
        spoken = window_start_s + picked[0].start if picked else None
        return Commit(lo, hi, text, spoken, reason)
