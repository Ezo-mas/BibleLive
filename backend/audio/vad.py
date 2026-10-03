"""Voice activity detection using WebRTC VAD."""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# webrtcvad is imported lazily: it pulls in pkg_resources (setuptools) at import
# time, which is noisy and slow, and the VAD object is only needed at runtime.
_webrtcvad: Optional[Any] = None


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
            return self.vad.is_speech(frame, self.sample_rate)
        except _load_webrtcvad().Error as e:
            logger.debug(f"VAD error (invalid frame): {e}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected VAD error: {e}")
            return False
