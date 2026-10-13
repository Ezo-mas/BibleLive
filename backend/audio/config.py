"""Configuration for the audio pipeline."""

from dataclasses import dataclass


@dataclass
class AudioPipelineConfig:
    """Configuration for audio pipeline."""

    # Queue sizes (bounded to prevent memory issues)
    audio_queue_maxsize: int = 1000  # ~10 seconds at 10ms chunks
    vad_queue_maxsize: int = 100  # ~100 speech segments

    # Retry configuration
    api_max_retries: int = 3
    api_retry_base_delay: float = 0.5  # seconds
    api_retry_max_delay: float = 5.0  # seconds
    api_timeout: float = 5.0  # seconds
    # Bearer token for POST /api/reference; required when the server runs with
    # BIBLELIVE_REQUIRE_AUTH=true. None sends no Authorization header.
    api_token: str | None = None
    # References wait here for the sender thread while the server is slow; when
    # full, the oldest waiting one is dropped. One still unsent after
    # send_max_age_s is dropped too, so a recovered server never receives (and
    # an auto-approve mode never displays) a reference from minutes ago.
    send_queue_maxsize: int = 20
    send_max_age_s: float = 30.0  # longer than one full retry schedule (~23.5s)

    # Audio capture restart after ffmpeg exits on its own (device unplugged,
    # driver reset). Delays double from base to max; they fall back to base once
    # a restarted stream has delivered capture_healthy_s of audio.
    capture_restart_base_delay: float = 1.0  # seconds
    capture_restart_max_delay: float = 30.0  # seconds
    capture_healthy_s: float = 10.0  # seconds of audio

    # Deduplication window
    dedup_window_seconds: float = 5.0  # Don't send same reference within 5s

    # Metrics logging interval
    metrics_log_interval: int = 60  # Log metrics every 60 seconds

    # Speech-to-text settings
    whisper_model: str = "small"
    whisper_device: str = "cuda"
    whisper_compute_type: str | None = None  # None = int8 (safe on all devices)
    language: str = "en"  # Spoken language: "en" or "lt"
    beam_size: int = 5  # Whisper beam search width; lower is faster
    cpu_threads: int = 0  # Whisper threads on CPU; 0 = library default
    vad_aggressiveness: int = 2  # 0=least, 3=most aggressive
    # Microphone name for ffmpeg (dshow device on Windows, pulse source on Linux,
    # avfoundation index on macOS). None = platform default; see capture.py.
    input_device: str | None = None

    # Utterance segmentation (see audio/segmenter.py); rounded to 30ms VAD frames.
    end_of_speech_s: float = 1.5  # silence that ends an utterance
    min_speech_s: float = 0.3  # shorter utterances are dropped as blips
    soft_utterance_s: float = 8.0  # past this, end at the next pause
    max_utterance_s: float = 12.0  # hard cap, for audio that never pauses

    # Runtime language-control server (stdlib http.server). Bound to loopback
    # by default; only expose it more widely behind a token.
    control_enabled: bool = True
    control_host: str = "127.0.0.1"
    control_port: int = 8001
    control_token: str | None = None  # optional bearer token for non-loopback hosts
