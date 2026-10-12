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

    # Deduplication window
    dedup_window_seconds: float = 5.0  # Don't send same reference within 5s

    # Metrics logging interval
    metrics_log_interval: int = 60  # Log metrics every 60 seconds

    # Speech-to-text settings
    whisper_model: str = "small"
    whisper_device: str = "cuda"
    whisper_compute_type: str | None = None  # None = int8 (safe on all devices)
    language: str = "en"  # Spoken language: "en" or "lt"
    vad_aggressiveness: int = 2  # 0=least, 3=most aggressive
    # Microphone name for ffmpeg (dshow device on Windows, pulse source on Linux,
    # avfoundation index on macOS). None = platform default; see capture.py.
    input_device: str | None = None

    # Runtime language-control server (stdlib http.server). Bound to loopback
    # by default; only expose it more widely behind a token.
    control_enabled: bool = True
    control_host: str = "127.0.0.1"
    control_port: int = 8001
    control_token: str | None = None  # optional bearer token for non-loopback hosts
