"""BibleLive audio pipeline package.

Replaces the former ``audio/biblelive_audio_pipeline.py`` monolith:

- ``audio.config``      - ``AudioPipelineConfig``
- ``audio.metrics``     - ``PipelineMetrics``
- ``audio.capture``     - ``AudioCapture`` (ffmpeg microphone capture)
- ``audio.vad``         - ``VoiceActivityDetector`` (WebRTC VAD), ``SileroVoiceActivityDetector``
- ``audio.stt``         - ``SpeechToText`` (faster-whisper)
- ``audio.sender``      - ``ReferenceSender`` (dedup + retry/backoff)
- ``audio.transcript_sender`` - ``TranscriptSender`` (best-effort transcripts for follow-along)
- ``audio.heartbeat``   - ``HeartbeatSender`` (periodic health report to the server)
- ``audio.control``     - ``LanguageControlServer`` (runtime language switching)
- ``audio.pipeline``    - ``BibleLiveAudioProcessor``
- ``audio.logging_config`` - ``configure_logging()`` (root logging setup)
- ``audio.cli``         - ``main()`` entry point
"""
