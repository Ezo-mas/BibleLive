"""Tests for audio pipeline logging configuration and audio-input accounting."""

import logging

from audio.capture import AudioCapture
from audio.logging_config import configure_logging, resolve_log_level
from audio.stt import SpeechToText


def test_resolve_log_level_precedence(monkeypatch):
    monkeypatch.setenv("BIBLELIVE_AUDIO_LOG_LEVEL", "DEBUG")
    assert resolve_log_level() == logging.DEBUG
    assert resolve_log_level("WARNING") == logging.WARNING  # explicit wins over env
    assert resolve_log_level(logging.ERROR) == logging.ERROR


def test_resolve_log_level_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("BIBLELIVE_AUDIO_LOG_LEVEL", "not-a-level")
    assert resolve_log_level() == logging.INFO


def test_configure_logging_installs_root_handler(monkeypatch, capsys):
    """Library modules must not configure logging; the CLI must, or output vanishes."""
    monkeypatch.delenv("BIBLELIVE_AUDIO_LOG_LEVEL", raising=False)

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    root.handlers.clear()
    try:
        assert configure_logging("INFO") == logging.INFO
        assert root.handlers, "configure_logging() must install a root handler"

        logging.getLogger("audio.capture").info("first audio chunk")
        assert "first audio chunk" in capsys.readouterr().out
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_capture_accounting_and_progress_logging(caplog):
    """Byte/second accounting answers 'is audio arriving?' without a device."""
    capture = AudioCapture(sample_rate=16000)
    assert capture.captured_seconds == 0.0

    with caplog.at_level(logging.INFO, logger="audio.capture"):
        capture.total_bytes_captured += 16000 * 2 * 10  # 10 seconds of 16-bit mono audio
        capture.total_chunks += 1000
        capture._log_capture_progress()
        assert capture.captured_seconds == 10.0
        assert any("Audio input active: 10s captured" in r.message for r in caplog.records)

    # Below the 10-second window: no duplicate progress line
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="audio.capture"):
        capture._log_capture_progress()
        assert not caplog.records


def _stt() -> object:
    """SpeechToText without loading a Whisper model (only pure helpers needed)."""
    return SpeechToText.__new__(SpeechToText)


def test_collapse_repetition_trims_decoder_loop():
    """Whisper loop 'Romans 8, or Romans 8, ...' collapses to one occurrence."""
    looping = "John chapter three verse sixteen, or " + "Romans 8, or " * 40

    text, loop = _stt()._collapse_repetition(looping)

    assert text == "John chapter three verse sixteen, or Romans 8,"
    assert loop is not None
    assert "repeated 40 times" in loop


def test_collapse_repetition_keeps_normal_transcripts():
    """Legitimate repeats and ordinary speech must pass through untouched."""
    normal = [
        "read Romans 8:28 and again Romans 8:28 for us",
        "Genesis chapter one verse one and Psalms twenty three",
        "thank you brother amen",
        "",
    ]
    for text in normal:
        cleaned, loop = _stt()._collapse_repetition(text)
        assert cleaned == text
        assert loop is None


def test_collapse_repetition_handles_tight_loop():
    """A tight 'the the the ...' loop is caught by the 2-word block rule."""
    text, loop = _stt()._collapse_repetition("the " * 10)
    assert text == "the the"  # exactly one block retained
    assert loop is not None
