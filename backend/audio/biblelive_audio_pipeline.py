#!/usr/bin/env python3
"""Compatibility shim for the old audio/biblelive_audio_pipeline.py module.

The pipeline now lives in the ``audio`` package:

- audio.config, audio.metrics   - configuration and metrics
- audio.capture, audio.vad      - audio capture and voice activity detection
- audio.stt                     - faster-whisper transcription
- audio.sender                  - reference delivery (dedup + retry/backoff)
- audio.pipeline                - BibleLiveAudioProcessor
- audio.cli                     - main() entry point

Import from ``audio`` instead.
"""

from .capture import AudioCapture  # noqa: F401
from .config import AudioPipelineConfig  # noqa: F401
from .metrics import PipelineMetrics  # noqa: F401
from .pipeline import BibleLiveAudioProcessor  # noqa: F401
from .sender import ReferenceSender  # noqa: F401
from .stt import (  # noqa: F401
    SUPPORTED_LANGUAGES,
    WHISPER_INITIAL_PROMPTS,
    SpeechToText,
)
from .vad import VoiceActivityDetector  # noqa: F401

if __name__ == "__main__":
    from .cli import main

    main()
