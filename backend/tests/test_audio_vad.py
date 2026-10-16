"""Tests for the VAD engines behind ``is_speech(frame)``."""

import argparse

import numpy as np
import pytest

from audio.config import AudioPipelineConfig

# Requires the optional audio extras (faster-whisper ships the Silero model).
pytestmark = pytest.mark.audio

FRAME_SAMPLES = 480  # 30 ms at 16 kHz, the pipeline's frame size


def _frames(samples: np.ndarray, size: int = FRAME_SAMPLES) -> list[bytes]:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    return [pcm[i : i + size].tobytes() for i in range(0, len(pcm) - size + 1, size)]


def _noisy_tone(seconds: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(int(16000 * seconds)) / 16000
    return (0.3 * np.sin(2 * np.pi * 220 * t) + 0.1 * rng.standard_normal(len(t))).astype(
        np.float32
    )


def test_silero_streaming_matches_the_bundled_batch_model():
    """30 ms frames re-chunked to 512-sample windows give the batch probabilities."""
    from faster_whisper.vad import get_vad_model

    from audio.vad import SileroVoiceActivityDetector

    vad = SileroVoiceActivityDetector()
    frames = _frames(_noisy_tone(3.0))
    pcm = np.frombuffer(b"".join(frames), dtype="<i2").astype(np.float32) / 32768.0
    pcm = np.concatenate([pcm, np.zeros(512, dtype=np.float32)])
    usable = (len(pcm) - 512) // 512 * 512
    # The batch call zeroes the tail of its input's last window in place, so
    # that one window is not a reference; one extra window keeps all real ones.
    expected = get_vad_model()(pcm[: usable + 512].copy()).reshape(-1)

    fed = 0
    for frame in frames:
        vad.is_speech(frame)
        fed += len(frame) // 2
        windows = fed // 512
        if windows:  # every sample is scored once, in order: none dropped
            assert vad.probability == pytest.approx(float(expected[windows - 1]), abs=1e-6)


@pytest.mark.parametrize("frame_samples", [160, 320, 480])
def test_silero_carries_leftover_samples_across_frames(frame_samples):
    from audio.vad import SileroVoiceActivityDetector

    vad = SileroVoiceActivityDetector()
    frames = _frames(_noisy_tone(1.0), frame_samples)
    for frame in frames:
        vad.is_speech(frame)

    assert len(vad._pending) == (len(frames) * frame_samples) % 512


def test_silero_calls_silence_silent_and_answers_from_the_latest_window():
    from audio.vad import SileroVoiceActivityDetector

    vad = SileroVoiceActivityDetector(threshold=0.5)
    silence = bytes(FRAME_SAMPLES * 2)

    assert not any(vad.is_speech(silence) for _ in range(50))
    assert vad.probability < 0.5


def test_silero_threshold_decides():
    from audio.vad import SileroVoiceActivityDetector

    vad = SileroVoiceActivityDetector(threshold=0.5)
    vad.probability = 0.4
    vad._pending = np.zeros(0, dtype=np.float32)
    assert not vad.is_speech(b"")  # no new window: the latest probability answers
    vad.threshold = 0.35
    assert vad.is_speech(b"")


@pytest.mark.parametrize("threshold", [0.0, 1.0, -0.1, 1.5])
def test_silero_rejects_thresholds_outside_zero_one(threshold):
    from audio.vad import SileroVoiceActivityDetector

    with pytest.raises(ValueError, match="threshold"):
        SileroVoiceActivityDetector(threshold=threshold)


def test_silero_rejects_other_sample_rates():
    from audio.vad import SileroVoiceActivityDetector

    with pytest.raises(ValueError, match="16 kHz"):
        SileroVoiceActivityDetector(sample_rate=8000)


def test_create_vad_builds_each_engine():
    from audio.vad import SileroVoiceActivityDetector, VoiceActivityDetector, create_vad

    assert isinstance(create_vad("webrtc"), VoiceActivityDetector)
    silero = create_vad("silero", silero_threshold=0.35)
    assert isinstance(silero, SileroVoiceActivityDetector)
    assert silero.threshold == 0.35
    with pytest.raises(ValueError, match="unknown VAD engine"):
        create_vad("other")


def test_webrtc_stays_the_default_vad():
    assert AudioPipelineConfig().vad == "webrtc"


def test_vad_flags_reach_the_config():
    from audio.cli import apply_tuning_arguments, build_parser

    config = AudioPipelineConfig()
    apply_tuning_arguments(
        config, build_parser().parse_args(["--vad", "silero", "--silero-threshold", "0.35"])
    )

    assert (config.vad, config.silero_threshold) == ("silero", 0.35)


def test_unknown_vad_flag_is_rejected():
    from audio.cli import _vad_engine, build_parser

    with pytest.raises(argparse.ArgumentTypeError, match="webrtc, silero"):
        _vad_engine("rnnoise")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--vad", "rnnoise"])
