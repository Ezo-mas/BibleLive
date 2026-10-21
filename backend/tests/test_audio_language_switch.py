"""Tests for runtime English/Lithuanian language switching.

Covers the three cooperating pieces:

- ``SpeechToText.set_language`` — atomic language + initial-prompt state;
- the VAD-side pending-switch mechanism (partial-utterance discard, tagging);
- parser ownership inside the transcription thread (per-language parsers with
  context reset on a language change).

No Whisper model, ffmpeg, GPU or network is needed: ``SpeechToText`` is built
with ``__new__`` and the processor is assembled from fakes.
"""

import socket
import threading
from queue import Queue

import pytest

from audio.config import AudioPipelineConfig
from audio.control import LanguageControlServer
from audio.metrics import PipelineMetrics
from audio.pipeline import BibleLiveAudioProcessor
from audio.segmenter import Segmenter, SegmentEvent
from audio.stt import SUPPORTED_LANGUAGES, WHISPER_INITIAL_PROMPTS, SpeechToText, Transcription
from core.verse_parser import ReferenceParser, StreamingParser

# Requires the optional audio extras (faster-whisper, webrtcvad, numpy).
pytestmark = [pytest.mark.audio, pytest.mark.usefixtures("fast_control_server_stop")]


# ---------------------------------------------------------------------------
# Fakes / builders
# ---------------------------------------------------------------------------
def _bare_stt() -> SpeechToText:
    """``SpeechToText`` without loading a Whisper model."""
    stt = SpeechToText.__new__(SpeechToText)
    stt.beam_size = 5
    return stt


class _FakeSTT:
    """Minimal SpeechToText stand-in for pipeline tests."""

    def __init__(self, language: str = "en"):
        self.language = language

    def set_language(self, language: str) -> None:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language '{language}'")
        self.language = language

    def transcribe_utterance(self, pcm, language=None):  # noqa: ARG002 - signature parity
        return Transcription(None, 0.0)


class _StaticSTT(_FakeSTT):
    """Returns a fixed transcript and records the language it was called with."""

    def __init__(
        self, transcript: str, language: str = "en", low_confidence_reason: str | None = None
    ):
        super().__init__(language)
        self._transcript = transcript
        self._low_confidence_reason = low_confidence_reason
        self.transcribe_languages = []

    def transcribe_utterance(self, pcm, language=None):  # noqa: ARG002 - signature parity
        self.transcribe_languages.append(language)
        return Transcription(self._transcript, 1.0, self._low_confidence_reason)


class _RecordingSender:
    def __init__(self):
        self.sent = []

    def submit(self, ref) -> None:  # noqa: ARG002 - recorded for assertions
        self.sent.append(ref)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class _RecordingTranscripts:
    """Stands in for the follow-along TranscriptSender; keeps what it was given."""

    def __init__(self):
        self.sent = []

    def submit(self, text: str, language: str) -> None:
        self.sent.append((text, language))

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class _FailingCapture:
    """Capture whose start always fails, as ffmpeg/a thread start can."""

    def __init__(self):
        self.started = False
        self.stopped = False

    def start_capture(self, device=None):  # noqa: ARG002 - signature parity
        self.started = True
        raise RuntimeError("capture boom")

    def stop_capture(self):
        self.stopped = True


def _processor(language: str = "en") -> BibleLiveAudioProcessor:
    """Assemble a processor without starting audio capture or loading a model."""
    proc = BibleLiveAudioProcessor.__new__(BibleLiveAudioProcessor)
    proc.config = AudioPipelineConfig(language=language)
    proc.is_running = False
    proc.stt = _FakeSTT(language)
    proc.audio_capture = None
    proc.control_server = None
    proc.vad_thread = None
    proc.transcription_thread = None
    proc.segmenter = Segmenter(lambda frame: True)
    proc.vad_queue = Queue(maxsize=10)
    proc.metrics = PipelineMetrics()
    proc.frame_duration_ms = 30
    proc._frame_duration_s = 0.03
    proc._capture_language = language
    proc._pending_language = None
    proc._pending_language_lock = threading.Lock()
    proc._local = threading.local()
    proc.heartbeat = None
    proc.sender = _RecordingSender()
    proc.transcript_sender = _RecordingTranscripts()
    proc._capture_down = False
    proc._consecutive_transcription_errors = 0
    proc._parser_issues = []
    return proc


def _install_parsers(proc: BibleLiveAudioProcessor) -> dict[str, StreamingParser]:
    parsers = {
        language: StreamingParser(ReferenceParser(language=language), language=language)
        for language in SUPPORTED_LANGUAGES
    }
    proc._local.streaming_parsers = parsers
    proc._local.last_parser_language = None
    return parsers


# ---------------------------------------------------------------------------
# SpeechToText.set_language
# ---------------------------------------------------------------------------
def test_set_language_en_to_lt():
    stt = _bare_stt()
    stt.set_language("en")
    assert stt.language == "en"
    assert stt.initial_prompt == WHISPER_INITIAL_PROMPTS["en"]

    stt.set_language("lt")
    assert stt.language == "lt"
    assert stt.initial_prompt == WHISPER_INITIAL_PROMPTS["lt"]


def test_set_language_lt_to_en():
    stt = _bare_stt()
    stt.set_language("lt")
    assert stt.language == "lt"

    stt.set_language("en")
    assert stt.language == "en"
    assert stt.initial_prompt == WHISPER_INITIAL_PROMPTS["en"]


def test_set_language_always_uses_the_matching_initial_prompt():
    stt = _bare_stt()
    assert WHISPER_INITIAL_PROMPTS["en"] != WHISPER_INITIAL_PROMPTS["lt"]
    for language in SUPPORTED_LANGUAGES:
        stt.set_language(language)
        assert stt.initial_prompt == WHISPER_INITIAL_PROMPTS[language]


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_initial_prompt_contains_no_citation_numbers(language):
    """Regression: Whisper echoes the prompt over silence, so a citation in it
    ("Romans 8:28") became a detected reference. Book names only."""
    prompt = WHISPER_INITIAL_PROMPTS[language]
    assert not any(ch.isdigit() for ch in prompt)
    for example_word in ("example", "pavyzd", "sixteen", "šešiolikt", "trečias"):
        assert example_word not in prompt.lower()


def test_set_language_rejects_invalid_language():
    stt = _bare_stt()
    stt.set_language("lt")
    with pytest.raises(ValueError):
        stt.set_language("fr")
    # A rejected switch leaves the previous language/prompt pair untouched.
    assert stt.language == "lt"
    assert stt.initial_prompt == WHISPER_INITIAL_PROMPTS["lt"]


# One second of 16 kHz int16 silence: long enough to clear the 0.5 s minimum.
_ONE_SECOND = bytes(2 * 16000)


def test_transcribe_reads_language_and_prompt_as_one_snapshot():
    """A switch mid-transcription must not mix a language with the other prompt."""
    stt = _bare_stt()
    stt.set_language("en")
    stt.sample_rate = 16000

    calls = []

    class _Model:
        def transcribe(self, audio, **kwargs):
            calls.append((kwargs["language"], kwargs["initial_prompt"]))
            stt.set_language("lt")  # swap the pair while the call is in flight
            return [], None

    stt.model = _Model()

    result = stt.transcribe_utterance(_ONE_SECOND)

    assert result.text is None
    assert calls == [("en", WHISPER_INITIAL_PROMPTS["en"])]
    assert stt.language == "lt"
    assert stt.initial_prompt == WHISPER_INITIAL_PROMPTS["lt"]


def test_transcribe_uses_explicit_language_over_global_state():
    """An 'en' utterance is transcribed in English even after a switch to lt."""
    stt = _bare_stt()
    stt.set_language("lt")  # switch happened after the utterance was queued
    stt.sample_rate = 16000

    calls = []

    class _Model:
        def transcribe(self, audio, **kwargs):
            calls.append((kwargs["language"], kwargs["initial_prompt"]))
            return [], None

    stt.model = _Model()

    stt.transcribe_utterance(_ONE_SECOND, "en")

    assert calls == [("en", WHISPER_INITIAL_PROMPTS["en"])]
    # The global state is only for un-tagged calls; it is left untouched.
    assert stt.language == "lt"
    assert stt.initial_prompt == WHISPER_INITIAL_PROMPTS["lt"]


def test_transcribe_passes_hotwords_with_the_matching_language(monkeypatch):
    """Hotwords travel with the language/prompt pair and are omitted when empty."""
    from audio import stt as stt_module

    monkeypatch.setitem(stt_module.WHISPER_HOTWORDS, "lt", "skyrius, eilutė")
    monkeypatch.setitem(stt_module.WHISPER_HOTWORDS, "en", "")
    stt = _bare_stt()
    calls = []

    class _Model:
        def transcribe(self, audio, **kwargs):
            calls.append((kwargs["language"], kwargs.get("hotwords", "<absent>")))
            return [], None

    stt.model = _Model()
    stt.sample_rate = 16000
    for language in ("lt", "en"):
        stt.set_language(language)
        stt.transcribe_utterance(_ONE_SECOND)

    # Empty hotwords must not be sent at all (older faster-whisper rejects the kwarg).
    assert calls == [("lt", "skyrius, eilutė"), ("en", "<absent>")]


def test_hotwords_default_off():
    """Measured worse than no hotwords on the small model; see stt.WHISPER_HOTWORDS."""
    from audio import stt as stt_module

    assert not any(stt_module.WHISPER_HOTWORDS.values())


def test_transcribe_rejects_invalid_explicit_language():
    stt = _bare_stt()
    stt.set_language("en")
    stt.sample_rate = 16000
    stt.model = None  # must not be reached

    with pytest.raises(ValueError):
        stt.transcribe_utterance(_ONE_SECOND, "fr")


# ---------------------------------------------------------------------------
# Pending language switch (control thread -> VAD thread)
# ---------------------------------------------------------------------------
def test_set_language_updates_stt_and_posts_switch():
    proc = _processor()
    proc.set_language("lt")

    assert proc.stt.language == "lt"  # STT updates immediately
    assert proc.current_language == "lt"
    assert proc._pending_language == "lt"  # VAD thread applies it later
    assert proc._capture_language == "en"  # control thread must not touch this


def test_set_language_rejects_invalid_language_without_posting_switch():
    proc = _processor()
    with pytest.raises(ValueError):
        proc.set_language("fr")
    assert proc._pending_language is None
    assert proc.stt.language == "en"


def test_consume_pending_switch_updates_capture_language():
    proc = _processor()
    proc.set_language("lt")

    proc._consume_pending_language_switch()

    assert proc._capture_language == "lt"
    assert proc._pending_language is None


def test_consume_pending_switch_discards_partial_speech():
    proc = _processor()
    proc.segmenter.speech_frames = [b"frame"] * 5
    proc.segmenter.silent_frames = 12
    proc.set_language("lt")

    proc._consume_pending_language_switch()

    assert proc.segmenter.speech_frames == []
    assert proc.segmenter.silent_frames == 0
    assert proc._capture_language == "lt"


def test_consume_switch_is_noop_without_a_request():
    proc = _processor()
    proc.segmenter.speech_frames = [b"frame"]
    proc.segmenter.silent_frames = 3

    proc._consume_pending_language_switch()

    assert proc.segmenter.speech_frames == [b"frame"]
    assert proc.segmenter.silent_frames == 3
    assert proc._capture_language == "en"


def test_new_utterances_receive_the_new_language_tag():
    proc = _processor()
    proc.set_language("lt")
    proc._consume_pending_language_switch()

    proc._queue_speech_segment(SegmentEvent("end", [b"a", b"b", b"c"]))

    language, frames = proc.vad_queue.get_nowait()
    assert language == "lt"
    assert frames == [b"a", b"b", b"c"]


def test_queued_utterances_keep_their_original_language_tag():
    proc = _processor()
    proc.vad_queue.put(("en", [b"old"]))
    proc.set_language("lt")

    proc._consume_pending_language_switch()

    # The queue is not drained by the switch: the old utterance stays, tag intact.
    language, frames = proc.vad_queue.get_nowait()
    assert language == "en"
    assert frames == [b"old"]
    assert proc.vad_queue.empty()


def test_queued_utterance_is_transcribed_in_its_capture_language():
    """A queued old-language utterance must not be re-languaged by a switch."""
    proc = _processor()
    _install_parsers(proc)
    proc.stt = _StaticSTT("Romans 8:28", language="en")
    proc.sender = _RecordingSender()
    proc.vad_queue.put(("en", [b""]))  # captured before the switch

    proc.set_language("lt")  # runtime switch before it is processed
    proc._consume_pending_language_switch()

    language, frames = proc.vad_queue.get_nowait()
    proc._process_utterance(language, frames)

    assert proc.stt.transcribe_languages == ["en"]  # not "lt"
    assert [ref.book for ref in proc.sender.sent] == ["Romans"]


# ---------------------------------------------------------------------------
# Parser ownership inside the transcription thread
# ---------------------------------------------------------------------------
def test_select_parser_matches_the_utterance_language():
    proc = _processor()
    parsers = _install_parsers(proc)

    assert proc._select_parser("en") is parsers["en"]
    assert proc._select_parser("lt") is parsers["lt"]
    assert proc._select_parser("en") is parsers["en"]


def test_select_parser_returns_none_for_unknown_language():
    proc = _processor()
    _install_parsers(proc)
    assert proc._select_parser("fr") is None


def test_parser_context_resets_on_language_change():
    proc = _processor()
    parsers = _install_parsers(proc)
    en = parsers["en"]

    proc._select_parser("en")
    en.feed("Romans chapter 8")
    ctx_before = en.ctx
    assert en.ctx.book == "Romans" and en.ctx.chapter == 8
    assert en.carry, "expected the English parser to retain carry context"

    # Switching away and back must reset *both* pieces of parser state: the
    # carry tokens and the inferred book/chapter context.
    proc._select_parser("lt")
    proc._select_parser("en")
    assert en.ctx is not ctx_before
    assert en.ctx.book is None
    assert en.ctx.chapter is None
    assert en.carry == []


def test_process_utterance_uses_the_speaking_languages_parser():
    proc = _processor()
    _install_parsers(proc)
    proc.stt = _StaticSTT("Romans 8:28")
    proc.sender = _RecordingSender()

    proc._process_utterance("en", [b""])
    assert [ref.book for ref in proc.sender.sent] == ["Romans"]

    # The Lithuanian parser does not recognise the English book name.
    proc._process_utterance("lt", [b""])
    assert len(proc.sender.sent) == 1


def test_low_confidence_transcription_marks_references_low():
    """A shaky decode must not send a reference that auto-approve would show."""
    proc = _processor()
    _install_parsers(proc)
    proc.stt = _StaticSTT("Romans 8:28", low_confidence_reason="no_speech_prob 0.91 > 0.6")
    proc.sender = _RecordingSender()

    proc._process_utterance("en", [b""])

    assert [(str(ref), ref.confidence) for ref in proc.sender.sent] == [("Romans 8:28", "low")]
    assert proc.metrics.references_low_confidence == 1


def test_sound_transcription_keeps_parser_confidence():
    proc = _processor()
    _install_parsers(proc)
    proc.stt = _StaticSTT("Romans chapter 8 verse 28")
    proc.sender = _RecordingSender()

    proc._process_utterance("en", [b""])

    assert [ref.confidence for ref in proc.sender.sent] == ["high"]
    assert proc.metrics.references_low_confidence == 0


def test_confident_transcript_is_submitted_for_follow_along_with_its_language():
    proc = _processor()
    proc.stt = _StaticSTT("šeimininkas reikalauja", language="lt")

    proc._process_utterance("lt", [b""])

    assert proc.transcript_sender.sent == [("šeimininkas reikalauja", "lt")]


def test_low_confidence_transcript_is_not_submitted_for_follow_along():
    """Its words may not be what was said, so it must not steer a hint either."""
    proc = _processor()
    proc.stt = _StaticSTT("Romans 8:28", low_confidence_reason="no_speech_prob 0.91 > 0.6")

    proc._process_utterance("en", [b""])

    assert proc.transcript_sender.sent == []


def test_empty_transcript_is_not_submitted_for_follow_along():
    proc = _processor()
    proc.stt = _StaticSTT("")

    proc._process_utterance("en", [b""])

    assert proc.transcript_sender.sent == []


# ---------------------------------------------------------------------------
# start() rollback
# ---------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_start_unwinds_control_server_when_capture_fails(monkeypatch):
    stopped = []

    class _RecordingControlServer:
        def __init__(self, **kwargs):  # noqa: ARG002 - pipeline wiring
            self.address = ("127.0.0.1", 8001)

        def start(self):
            pass

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr("audio.pipeline.LanguageControlServer", _RecordingControlServer)

    proc = _processor()
    proc.audio_capture = _FailingCapture()

    with pytest.raises(RuntimeError, match="capture boom"):
        proc.start()

    assert stopped == [True]
    assert proc.control_server is None
    assert proc.is_running is False
    assert proc.audio_capture.stopped is True


def test_start_guard_rejects_second_start_without_disturbing_live_run():
    proc = _processor()
    sentinel_server = object()
    proc.control_server = sentinel_server
    proc.is_running = True  # a pipeline is already live
    proc.audio_capture = _FailingCapture()  # would explode if start() proceeded

    with pytest.raises(RuntimeError, match="already running"):
        proc.start()

    # The live run must be untouched: no rebind, no rollback, no capture stop.
    assert proc.is_running is True
    assert proc.control_server is sentinel_server
    assert proc.audio_capture.started is False
    assert proc.audio_capture.stopped is False


def test_start_is_allowed_again_after_a_failed_start():
    proc = _processor()
    proc.audio_capture = _FailingCapture()

    with pytest.raises(RuntimeError, match="capture boom"):
        proc.start()
    assert proc.is_running is False

    # The guard must not block a retry after a cleanly unwound failure.
    with pytest.raises(RuntimeError, match="capture boom"):
        proc.start()


def test_start_releases_control_port_when_capture_fails():
    port = _free_port()
    proc = _processor()
    proc.config = AudioPipelineConfig(control_port=port)
    proc.audio_capture = _FailingCapture()

    with pytest.raises(RuntimeError, match="capture boom"):
        proc.start()

    assert proc.control_server is None
    assert proc.is_running is False

    # The port must be free again: a failed startup leaked nothing.
    rebind = LanguageControlServer(lambda: "en", lambda lang: None, port=port)
    rebind.start()
    try:
        assert rebind.address is not None
    finally:
        rebind.stop()


# ---------------------------------------------------------------------------
# Held chapter-only references are delivered, not dropped
# ---------------------------------------------------------------------------
def _install_holding_parsers(proc: BibleLiveAudioProcessor) -> dict[str, StreamingParser]:
    """Parsers as the live pipeline builds them: chapter-only refs are held."""
    parsers = {
        language: StreamingParser(
            ReferenceParser(language=language), language=language, hold_chapter_only=True
        )
        for language in SUPPORTED_LANGUAGES
    }
    proc._local.streaming_parsers = parsers
    proc._local.last_parser_language = None
    return parsers


def test_language_switch_delivers_the_held_chapter_reference():
    """The verse for a held "Romans 8" can no longer arrive in the old language."""
    proc = _processor("en")
    parsers = _install_holding_parsers(proc)
    assert proc._select_parser("en") is parsers["en"]
    assert parsers["en"].feed("turn to Romans chapter 8", 0.0) == []  # held

    proc._select_parser("lt")

    assert [str(ref) for ref in proc.sender.sent] == ["Romans 8"]
    assert parsers["en"].flush() == []  # released exactly once


class _ClosableLookup:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_transcription_thread_exit_delivers_held_reference_and_closes_lookup(monkeypatch):
    """stop() runs on another thread, so only this thread can do either."""
    proc = _processor("en")
    lookup = _ClosableLookup()

    def init():
        parsers = _install_holding_parsers(proc)
        parsers["en"].feed("turn to Romans chapter 8", 0.0)
        proc._local.bible_lookup = lookup

    monkeypatch.setattr(proc, "_init_thread_local_parsers", init)
    proc.is_running = False  # stopping: the loop exits straight away

    proc._transcription_loop()

    assert [str(ref) for ref in proc.sender.sent] == ["Romans 8"]
    assert lookup.closed is True
    assert proc._local.bible_lookup is None


def test_initial_prompts_fit_whisper_prompt_window():
    # faster-whisper keeps only the last 223 prompt tokens, so a longer prompt
    # silently loses its first book names (the Lithuanian one hit 224).
    import glob
    import os

    tokenizers = pytest.importorskip("tokenizers")
    found = glob.glob(
        os.path.expanduser("~/.cache/huggingface/hub/*whisper-large-v3*/snapshots/*/tokenizer.json")
    )
    if not found:
        pytest.skip("no cached Whisper tokenizer")
    tok = tokenizers.Tokenizer.from_file(found[0])
    for language, prompt in WHISPER_INITIAL_PROMPTS.items():
        assert len(tok.encode(" " + prompt).ids) <= 223, language
