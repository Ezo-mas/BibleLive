"""Tests for audio pipeline logging configuration and audio-input accounting."""

import logging
from types import SimpleNamespace

import pytest

from audio.capture import AudioCapture
from audio.logging_config import configure_logging, resolve_log_level
from audio.stt import _REFERENCE_CORRECTIONS, SpeechToText, segment_low_confidence_reason
from core.books import CANONICAL_BOOKS

# Requires the optional audio extras (faster-whisper, webrtcvad, numpy).
pytestmark = pytest.mark.audio


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


def test_fix_common_transcription_errors_recognizes_timothy():
    """Regression: the regex typo 'timon' left both Timothy epistles uncorrected."""
    stt = _stt()

    assert stt._fix_common_transcription_errors("1 timothy 1 1") == "1 timothy 1:1"
    assert stt._fix_common_transcription_errors("2 timothy 1 7") == "2 timothy 1:7"
    # A merged transcription must no longer be passed through untouched.
    assert stt._fix_common_transcription_errors("1timothy312") != "1timothy312"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Already-formatted Whisper output must pass through unchanged
        # (previously "John 3:16" became "John 3:1-6").
        ("John 3:16", "John 3:16"),
        ("Romans 8:28.", "Romans 8:28."),
        ("Psalms 119:105", "Psalms 119:105"),
        ("1 Corinthians 13:4", "1 Corinthians 13:4"),
        ("Genesis 1:1", "Genesis 1:1"),
        # Space-separated numbers become chapter:verse, multi-digit included.
        ("turn to John 3 16", "turn to John 3:16"),
        ("Psalms 119 105", "Psalms 119:105"),
        ("Mark 11 12", "Mark 11:12"),
        # A trailing number after chapter:verse becomes a range.
        ("Mark 1:12 15", "Mark 1:12-15"),
        ("John 3 16 17", "John 3:16-17"),
        # Merged digits are split only when exactly one split is valid.
        ("John 316", "John 3:16"),
        ("Romans 828", "Romans 8:28"),
        ("1timothy312", "1timothy 3:12"),
        # A valid chapter number is a chapter reference, not merged digits.
        ("Romans 12", "Romans 12"),
        ("Psalms 119", "Psalms 119"),
        # Ambiguous merged digits are left for the parser instead of guessed.
        ("mark112", "mark112"),
    ],
)
def test_fix_common_transcription_errors_keeps_multi_digit_numbers(text, expected):
    """Regression: optional whitespace let the regex split multi-digit numbers."""
    assert _stt()._fix_common_transcription_errors(text) == expected


def test_reference_corrections_cover_every_canonical_book():
    """P1-3: the STT correction regex is generated from the shared canon.

    If a book is added or renamed, this fails until the patterns pick it up —
    catching the drift that produced 'timon' instead of 'timothy'.
    """
    for pattern in _REFERENCE_CORRECTIONS:
        for book in CANONICAL_BOOKS:
            name_pattern = r"\s?".join(book.name.lower().split())
            assert name_pattern in pattern, f"{book.name!r} missing from a correction pattern"


def test_segment_low_confidence_reason_uses_whisper_thresholds():
    assert segment_low_confidence_reason(-0.3, 0.1, 1.5) is None
    assert "no_speech_prob" in segment_low_confidence_reason(-0.3, 0.9, 1.5)
    assert "avg_logprob" in segment_low_confidence_reason(-1.4, 0.1, 1.5)
    assert "compression_ratio" in segment_low_confidence_reason(-0.3, 0.1, 3.1)


def _segment(text, avg_logprob=-0.3, no_speech_prob=0.1, compression_ratio=1.5):
    return SimpleNamespace(
        text=text,
        avg_logprob=avg_logprob,
        no_speech_prob=no_speech_prob,
        compression_ratio=compression_ratio,
    )


def _stt_returning(*segments):
    """A model-free SpeechToText whose Whisper call yields ``segments``."""
    stt = _stt()
    stt.set_language("en")
    stt.sample_rate = 16000
    stt.audio_buffer = [0.0] * 16000

    class _Model:
        def transcribe(self, audio, **kwargs):
            return iter(segments), None

    stt.model = _Model()
    return stt


def test_transcribe_utterance_flags_a_shaky_segment():
    """One probably-silent segment makes the whole utterance low-confidence."""
    stt = _stt_returning(
        _segment(" Turn with me."), _segment(" Romans chapter 8 verse 28.", no_speech_prob=0.9)
    )

    result = stt.transcribe_utterance()

    assert result.text == "Turn with me. Romans chapter 8 verse 28."
    assert result.low_confidence_reason is not None
    assert "no_speech_prob" in result.low_confidence_reason


def test_transcribe_utterance_trusts_sound_segments():
    stt = _stt_returning(_segment(" Turn with me to Romans chapter 8 verse 28."))

    result = stt.transcribe_utterance()

    assert result.text == "Turn with me to Romans chapter 8 verse 28."
    assert result.low_confidence_reason is None


def test_transcribe_utterance_flags_a_trimmed_repetition_loop():
    stt = _stt_returning(_segment(" " + "Romans 8, or " * 10))

    result = stt.transcribe_utterance()

    assert result.text == "Romans 8, or"
    assert result.low_confidence_reason is not None
    assert "repetition loop" in result.low_confidence_reason
