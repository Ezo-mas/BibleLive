"""BibleLive audio pipeline package.

Replaces the former ``audio/biblelive_audio_pipeline.py`` monolith:

- ``audio.config``      - ``AudioPipelineConfig``
- ``audio.metrics``     - ``PipelineMetrics``
- ``audio.capture``     - ``AudioCapture`` (ffmpeg microphone capture)
- ``audio.vad``         - ``VoiceActivityDetector`` (WebRTC VAD), ``SileroVoiceActivityDetector``
- ``audio.stt``         - ``SpeechToText`` (faster-whisper)
- ``audio.sender``      - ``ReferenceSender`` (dedup + retry/backoff)
- ``audio.heartbeat``   - ``HeartbeatSender`` (periodic health report to the server)
- ``audio.control``     - ``LanguageControlServer`` (runtime language switching)
- ``audio.pipeline``    - ``BibleLiveAudioProcessor``
- ``audio.logging_config`` - ``configure_logging()`` (root logging setup)
- ``audio.cli``         - ``main()`` entry point
"""

from .capture import AudioCapture
from .config import AudioPipelineConfig
from .control import LanguageControlServer
from .logging_config import configure_logging
from .metrics import PipelineMetrics
from .pipeline import BibleLiveAudioProcessor
from .sender import ReferenceSender
from .stt import SUPPORTED_LANGUAGES, WHISPER_INITIAL_PROMPTS, SpeechToText
from .vad import VoiceActivityDetector

__all__ = [
    "SUPPORTED_LANGUAGES",
    "WHISPER_INITIAL_PROMPTS",
    "AudioCapture",
    "AudioPipelineConfig",
    "BibleLiveAudioProcessor",
    "LanguageControlServer",
    "PipelineMetrics",
    "ReferenceSender",
    "SpeechToText",
    "VoiceActivityDetector",
    "configure_logging",
]
