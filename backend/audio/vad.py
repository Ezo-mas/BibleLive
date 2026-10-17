"""Voice activity detection: WebRTC VAD (default) or Silero VAD."""

import logging
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)

# webrtcvad is imported lazily: it pulls in pkg_resources (setuptools) at import
# time, which is noisy and slow, and the VAD object is only needed at runtime.
_webrtcvad: Any = None


def _load_webrtcvad() -> Any:
    """Import and cache the webrtcvad module."""
    global _webrtcvad
    if _webrtcvad is None:
        import webrtcvad

        _webrtcvad = webrtcvad
    return _webrtcvad


class VoiceActivityDetector:
    """Handles voice activity detection using WebRTC VAD"""

    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2):
        webrtcvad = _load_webrtcvad()
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        self.frame_duration_ms = 30  # WebRTC VAD requires 10, 20, or 30 ms frames

    def is_speech(self, frame: bytes) -> bool:
        """Check if audio frame contains speech"""
        try:
            return bool(self.vad.is_speech(frame, self.sample_rate))
        except _load_webrtcvad().Error as e:
            logger.debug(f"VAD error (invalid frame): {e}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected VAD error: {e}")
            return False


def silero_neg_threshold(threshold: float, neg_threshold: float | None = None) -> float:
    """The probability Silero speech must drop below to end (faster-whisper's default).

    Rounded so 0.5 gives 0.35, not 0.35000000000000003, and never above the threshold.
    """
    if neg_threshold is not None:
        return neg_threshold
    return min(round(max(threshold - 0.15, 0.01), 6), threshold)


class SileroVoiceActivityDetector:
    """Silero VAD (v6, the ONNX model bundled with faster-whisper) behind the
    same ``is_speech(frame)`` interface as the WebRTC detector.

    The pipeline keeps its 30 ms frames; Silero needs 512-sample windows at
    16 kHz, so each frame's samples are buffered and every complete window is
    scored in order (leftover samples carry over to the next frame).

    Speech starts when a window's probability reaches ``threshold`` and lasts
    until one drops below ``neg_threshold`` (default ``threshold - 0.15``, as
    in faster-whisper's own Silero use), so the dips inside a phrase stay
    voiced. ``neg_threshold == threshold`` is a plain per-window threshold.
    """

    WINDOW_SAMPLES = 512
    CONTEXT_SAMPLES = 64  # the model expects each window prefixed by the last 64 samples

    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 0.5,
        neg_threshold: float | None = None,
    ):
        if sample_rate != 16000:
            raise ValueError(f"Silero VAD runs at 16 kHz here, got {sample_rate}")
        if not 0.0 < threshold < 1.0:
            raise ValueError(f"threshold must be between 0 and 1, got {threshold}")
        neg_threshold = silero_neg_threshold(threshold, neg_threshold)
        if not 0.0 < neg_threshold <= threshold:
            raise ValueError(
                f"neg_threshold must be in (0, threshold={threshold}], got {neg_threshold}"
            )
        from faster_whisper.vad import get_vad_model

        try:
            self._session = get_vad_model().session
            # One silent window up front: a model/API mismatch fails here, at
            # startup, instead of silencing every frame later.
            self._session.run(
                None,
                {
                    "input": np.zeros((1, self.CONTEXT_SAMPLES + self.WINDOW_SAMPLES), np.float32),
                    "h": np.zeros((1, 1, 128), np.float32),
                    "c": np.zeros((1, 1, 128), np.float32),
                },
            )
        except Exception as e:
            raise RuntimeError(
                "Silero VAD needs the Silero v6 model bundled with faster-whisper >= 1.2"
            ) from e
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.neg_threshold = neg_threshold
        self.frame_duration_ms = 30
        self._pending = np.zeros(0, dtype=np.float32)
        self._context = np.zeros(self.CONTEXT_SAMPLES, dtype=np.float32)
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self.probability = 0.0  # of the latest scored window
        self.triggered = False  # inside speech (hysteresis state)

    def is_speech(self, frame: bytes) -> bool:
        """Check if audio frame contains speech (16-bit mono PCM)."""
        try:
            samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
            self._pending = np.concatenate([self._pending, samples])
            while len(self._pending) >= self.WINDOW_SAMPLES:
                window = self._pending[: self.WINDOW_SAMPLES]
                model_input = np.concatenate([self._context, window])[np.newaxis, :]
                prob, self._h, self._c = self._session.run(
                    None, {"input": model_input, "h": self._h, "c": self._c}
                )
                # Consumed only once scored: a failed run keeps the window.
                self._pending = self._pending[self.WINDOW_SAMPLES :]
                self._context = window[-self.CONTEXT_SAMPLES :]
                self.probability = float(np.asarray(prob).reshape(-1)[0])
                if self.probability >= self.threshold:
                    self.triggered = True
                elif self.probability < self.neg_threshold:
                    self.triggered = False
        except Exception as e:
            logger.warning(f"Unexpected Silero VAD error: {e}")
            return False
        return self.triggered


class SpeechDetector(Protocol):
    def is_speech(self, frame: bytes) -> bool: ...


VAD_ENGINES = ("webrtc", "silero")


def create_vad(
    engine: str,
    aggressiveness: int = 2,
    silero_threshold: float = 0.5,
    silero_neg_threshold: float | None = None,
) -> SpeechDetector:
    """Build the configured VAD: ``webrtc`` (default) or ``silero``."""
    if engine == "webrtc":
        return VoiceActivityDetector(aggressiveness=aggressiveness)
    if engine == "silero":
        return SileroVoiceActivityDetector(
            threshold=silero_threshold, neg_threshold=silero_neg_threshold
        )
    raise ValueError(f"unknown VAD engine {engine!r}; expected one of {VAD_ENGINES}")
