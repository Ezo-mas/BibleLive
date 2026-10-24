"""Tests for the GGUF speech engines (``--stt``), spelling maps and two-model agreement.

No model, GPU or transcribe.cpp install is needed: models are fakes, and the
``transcribe_cpp`` module is replaced where the real wrapper is exercised.
"""

import sys
import threading
import types

import numpy as np
import pytest
from processor_factory import make_processor

from audio.config import AudioPipelineConfig
from audio.ensemble import merge_agreement
from audio.pipeline import BibleLiveAudioProcessor
from audio.spelling_maps import MappedStreamingParser, spelling_map
from audio.stt import Transcription
from audio.stt_gguf import (
    GgufSpeechToText,
    MultiModelSpeechToText,
    create_gguf_speech_to_text,
    engine_models,
)
from core.verse_parser import Reference, ReferenceParser, StreamingParser

# audio.stt imports faster-whisper (optional audio extras).
pytestmark = pytest.mark.audio

ONE_SECOND = b"\0\0" * 16000


def _ref(book: str, chapter: int, verse: int | None, confidence: str = "high") -> Reference:
    return Reference(book=book, chapter=chapter, verse_start=verse, confidence=confidence)


# ---------------------------------------------------------------------------
# Spelling maps
# ---------------------------------------------------------------------------
def test_each_model_has_its_own_map():
    kmynas, canary = spelling_map("kmynas", "lt"), spelling_map("canary", "lt")
    assert kmynas is not None and canary is not None
    assert kmynas("Psmė dvidešimt trečia") == "Psalmė dvidešimt trečia"
    assert canary("Psmė dvidešimt trečia") == "Psmė dvidešimt trečia"  # not Canary's error
    assert canary("Salme 23") == "psalme 23"


def test_whisper_and_other_languages_have_no_map():
    assert spelling_map("whisper", "lt") is None
    assert spelling_map("kmynas", "en") is None


def test_mapped_parser_parses_the_corrected_text():
    def parse(parser: StreamingParser) -> list[str]:
        return [str(r) for r in parser.feed("Psmė dvidešimt trečia, pirma eilutė", 0.0)]

    rewrite = spelling_map("kmynas", "lt")
    assert rewrite is not None
    mapped = MappedStreamingParser(ReferenceParser(language="lt"), spelling_map=rewrite)
    assert parse(mapped) == ["Psalms 23:1"]
    assert parse(StreamingParser(ReferenceParser(language="lt"))) == []


def test_kmynas_map_reads_ievute_as_the_verse_word():
    rewrite = spelling_map("kmynas", "lt")
    assert rewrite is not None
    assert rewrite("septinta Ievutė ir Dievo ramybė") == "septinta eilutė ir Dievo ramybė"
    assert rewrite("septintoje ievutėje") == "septintoje eilutėje"
    # Other forms of the name stay ("Ievutei" is "to Ievutė").
    assert rewrite("pasakė Ievutei") == "pasakė Ievutei"


# ---------------------------------------------------------------------------
# Agreement merge
# ---------------------------------------------------------------------------
def test_agreed_reference_keeps_the_higher_confidence():
    merged = merge_agreement([_ref("John", 3, 16, "medium")], [_ref("John", 3, 16, "high")])
    assert [(str(r), r.confidence) for r in merged] == [("John 3:16", "high")]


def test_single_model_references_become_low():
    merged = merge_agreement(
        [_ref("John", 3, 16), _ref("Romans", 8, 28)],
        [_ref("John", 3, 16), _ref("Psalms", 23, 1)],
    )
    assert [(str(r), r.confidence) for r in merged] == [
        ("John 3:16", "high"),
        ("Romans 8:28", "low"),
        ("Psalms 23:1", "low"),
    ]


def _inferred(book: str, chapter: int, verse: int) -> Reference:
    return Reference(book, chapter, verse, None, "low", inferred=True)


def test_context_verse_both_models_inferred_becomes_medium():
    # "Ir toliau trisdešimta eilutė" with 2 Kings 6 running, in both transcripts.
    merged = merge_agreement(
        [_inferred("2 Kings", 6, 30)], [_inferred("2 Kings", 6, 30)], promote_inferred=True
    )
    assert [(str(r), r.confidence) for r in merged] == [("2 Kings 6:30", "medium")]


def test_context_verse_is_not_promoted_by_default():
    # Callers turn promotion on only when neither decode was shaky.
    merged = merge_agreement([_inferred("2 Kings", 6, 30)], [_inferred("2 Kings", 6, 30)])
    assert [(str(r), r.confidence) for r in merged] == [("2 Kings 6:30", "low")]


def test_context_range_both_inferred_against_its_start_keeps_the_range():
    # "nuo 8 iki 16 eilutės" from one model, only "nuo 8" from the other.
    full = Reference("1 Kings", 17, 8, 16, "low", inferred=True)
    merged = merge_agreement([_inferred("1 Kings", 17, 8)], [full], promote_inferred=True)
    assert [(str(r), r.confidence) for r in merged] == [("1 Kings 17:8-16", "medium")]


def test_context_verse_stays_low_from_one_model_or_one_inferred():
    # One model alone, or agreement where one side was a plain low parse
    # (not inferred), keeps the operator in the loop.
    alone = merge_agreement([_inferred("2 Kings", 6, 30)], [])
    mixed = merge_agreement([_inferred("2 Kings", 6, 30)], [_ref("2 Kings", 6, 30, "low")])
    assert [(str(r), r.confidence) for r in alone + mixed] == [
        ("2 Kings 6:30", "low"),
        ("2 Kings 6:30", "low"),
    ]


def test_merge_reports_each_reference_once():
    merged = merge_agreement(
        [_ref("John", 3, 16), _ref("John", 3, 16)], [_ref("John", 3, 16), _ref("John", 3, 16)]
    )
    assert [str(r) for r in merged] == ["John 3:16"]


def test_merge_with_nothing_from_either_model():
    assert merge_agreement([], []) == []


def _range(book: str, chapter: int, start: int, end: int, confidence: str = "high") -> Reference:
    return Reference(
        book=book, chapter=chapter, verse_start=start, verse_end=end, confidence=confidence
    )


@pytest.mark.parametrize("range_first", [True, False])
def test_range_and_its_start_verse_agree_and_the_range_is_kept(range_first):
    # One model dropped the range's second number ("pirmąjį lūtę" for 40:1-2).
    full, start = _range("Psalms", 40, 1, 2, "medium"), _ref("Psalms", 40, 1, "high")
    primary, alternate = ([full], [start]) if range_first else ([start], [full])
    merged = merge_agreement(primary, alternate)
    assert [(str(r), r.confidence) for r in merged] == [("Psalms 40:1-2", "high")]


def test_range_does_not_agree_with_another_start_or_a_chapter():
    merged = merge_agreement(
        [_range("Psalms", 40, 1, 2), _ref("John", 3, None)],
        [_ref("Psalms", 40, 2), _ref("John", 3, 16)],
    )
    assert [(str(r), r.confidence) for r in merged] == [
        ("Psalms 40:1-2", "low"),
        ("John 3", "low"),
        ("Psalms 40:2", "low"),
        ("John 3:16", "low"),
    ]


# ---------------------------------------------------------------------------
# MultiModelSpeechToText
# ---------------------------------------------------------------------------
class _FakeModel:
    def __init__(self, name: str, text: str | None, failed: bool = False):
        self.name = name
        self.text = text
        self.failed = failed
        self.calls: list[str] = []

    def transcribe(self, audio: np.ndarray, language: str) -> Transcription:  # noqa: ARG002
        self.calls.append(language)
        return Transcription(self.text, 10.0, failed=self.failed, source=self.name)


class _FakeWhisper:
    def __init__(self):
        self.calls: list[str | None] = []

    def transcribe_utterance(self, pcm: bytes, language: str | None = None) -> Transcription:  # noqa: ARG002
        self.calls.append(language)
        return Transcription("John 3:16", 5.0)


def _whisper_factory(created: list):
    def make():
        whisper = _FakeWhisper()
        created.append(whisper)
        return whisper

    return make


def test_one_model_returns_its_own_transcription():
    stt = MultiModelSpeechToText([_FakeModel("kmynas", "jono")], _whisper_factory([]), "lt")
    result = stt.transcribe_utterance(ONE_SECOND)
    assert (result.text, result.source, result.alternate) == ("jono", "kmynas", None)


def test_two_models_return_the_second_as_alternate():
    stt = MultiModelSpeechToText(
        [_FakeModel("kmynas", "jono"), _FakeModel("canary", "Jono", failed=False)],
        _whisper_factory([]),
        "lt",
    )
    result = stt.transcribe_utterance(ONE_SECOND, "lt")
    assert result.text == "jono"
    assert result.alternate is not None and result.alternate.text == "Jono"
    assert result.alternate.source == "canary"
    assert result.duration_ms == 20.0


def test_either_model_failing_is_reported():
    stt = MultiModelSpeechToText(
        [_FakeModel("kmynas", "jono"), _FakeModel("canary", None, failed=True)],
        _whisper_factory([]),
        "lt",
    )
    assert stt.transcribe_utterance(ONE_SECOND).failed


def test_short_audio_is_skipped_like_whisper():
    model = _FakeModel("kmynas", "jono")
    stt = MultiModelSpeechToText([model], _whisper_factory([]), "lt")
    assert stt.transcribe_utterance(b"\0\0" * 4000).text is None
    assert model.calls == []


def test_english_loads_whisper_once_in_the_background():
    created: list = []
    model = _FakeModel("kmynas", "jono")
    stt = MultiModelSpeechToText([model], _whisper_factory(created), "lt")
    assert created == []  # a Lithuanian-only service never loads Whisper

    stt.set_language("en")
    assert stt.wait_for_whisper(timeout=5)
    assert stt.transcribe_utterance(ONE_SECOND).text == "John 3:16"
    stt.set_language("lt")
    stt.set_language("en")  # switching back and forth loads it only once
    stt.transcribe_utterance(ONE_SECOND, "en")
    assert len(created) == 1 and created[0].calls == ["en", "en"]
    assert model.calls == []


def _blocking_whisper_factory(release: threading.Event, created: list):
    def make():
        release.wait(5)
        whisper = _FakeWhisper()
        created.append(whisper)
        return whisper

    return make


def test_lithuanian_keeps_flowing_while_whisper_loads():
    # Live test 2026-10-18: the switch to English stalled every utterance ~1 min.
    release = threading.Event()
    created: list = []
    model = _FakeModel("kmynas", "jono")
    stt = MultiModelSpeechToText([model], _blocking_whisper_factory(release, created), "lt")

    stt.set_language("en")
    # An English utterance during the load is skipped as unheard, not failed...
    skipped = stt.transcribe_utterance(ONE_SECOND, "en")
    assert (skipped.text, skipped.failed) == (None, False)
    # ...and a Lithuanian one queued behind it is transcribed at once.
    assert stt.transcribe_utterance(ONE_SECOND, "lt").text == "jono"

    release.set()
    assert stt.wait_for_whisper(timeout=5)
    assert stt.transcribe_utterance(ONE_SECOND, "en").text == "John 3:16"
    assert len(created) == 1


def test_failed_whisper_load_is_reported_and_retried_on_the_next_switch():
    calls: list[int] = []

    def broken():
        calls.append(1)
        raise RuntimeError("no model download")

    stt = MultiModelSpeechToText([_FakeModel("kmynas", "x")], broken, "lt")
    stt.set_language("en")
    assert not stt.wait_for_whisper(timeout=5)
    assert stt.transcribe_utterance(ONE_SECOND, "en").failed
    assert stt.transcribe_utterance(ONE_SECOND, "en").failed
    assert len(calls) == 1  # not retried on every utterance
    stt.set_language("en")  # the operator switches again: one more try
    stt.wait_for_whisper(timeout=5)
    assert len(calls) == 2


def test_starting_in_english_loads_whisper_at_start():
    created: list = []
    stt = MultiModelSpeechToText([_FakeModel("kmynas", "x")], _whisper_factory(created), "en")
    assert len(created) == 1
    assert stt.transcribe_utterance(ONE_SECOND).text == "John 3:16"


def test_starting_in_english_fails_loudly_when_whisper_cannot_load():
    def broken():
        raise RuntimeError("no model download")

    with pytest.raises(RuntimeError):
        MultiModelSpeechToText([_FakeModel("kmynas", "x")], broken, "en")


def test_unsupported_language_is_rejected():
    stt = MultiModelSpeechToText([_FakeModel("kmynas", "x")], _whisper_factory([]), "lt")
    with pytest.raises(ValueError):
        stt.set_language("fr")
    with pytest.raises(ValueError):
        stt.transcribe_utterance(ONE_SECOND, "fr")


def test_engine_models():
    assert engine_models("whisper") == []
    assert engine_models("kmynas") == ["kmynas"]
    assert engine_models("kmynas+canary") == ["kmynas", "canary"]
    with pytest.raises(ValueError):
        engine_models("parakeet")


def test_missing_model_path_is_a_clear_error():
    with pytest.raises(ValueError, match="--canary-model"):
        create_gguf_speech_to_text(
            "kmynas+canary",
            {"kmynas": "k.gguf", "canary": None},
            device="cpu",
            cpu_threads=0,
            language="lt",
            whisper_factory=_FakeWhisper,
        )


# ---------------------------------------------------------------------------
# GgufSpeechToText over a fake transcribe_cpp
# ---------------------------------------------------------------------------
class _OutputRepetition(Exception):
    def __init__(self, partial_result):
        super().__init__("repetition")
        self.partial_result = partial_result


@pytest.fixture
def fake_transcribe_cpp(monkeypatch):
    behaviour: dict = {"run": lambda audio, language: types.SimpleNamespace(text=" jono ")}
    loaded: list = []

    class Session:
        def run(self, audio, language):
            return behaviour["run"](audio, language)

    class Model:
        def __init__(self, path, backend):
            loaded.append((path, backend))
            self.backend = backend.upper()

        def session(self, n_threads):  # noqa: ARG002
            return Session()

    module = types.SimpleNamespace(
        Model=Model, errors=types.SimpleNamespace(OutputRepetition=_OutputRepetition)
    )
    monkeypatch.setitem(sys.modules, "transcribe_cpp", module)
    return behaviour, loaded


def test_gguf_model_backend_follows_the_device(fake_transcribe_cpp):
    _, loaded = fake_transcribe_cpp
    GgufSpeechToText("kmynas", "k.gguf", device="cpu")
    GgufSpeechToText("kmynas", "k.gguf", device="cuda")
    assert loaded == [("k.gguf", "cpu"), ("k.gguf", "auto")]


def test_gguf_transcript_is_cleaned(fake_transcribe_cpp):
    stt = GgufSpeechToText("kmynas", "k.gguf")
    result = stt.transcribe(np.zeros(16000, dtype=np.float32), "lt")
    assert (result.text, result.source, result.low_confidence_reason) == ("jono", "kmynas", None)


def test_gguf_output_repetition_keeps_the_partial_text_as_low(fake_transcribe_cpp):
    behaviour, _ = fake_transcribe_cpp

    def repeat(audio, language):  # noqa: ARG001
        raise _OutputRepetition(types.SimpleNamespace(text="jono trečias"))

    behaviour["run"] = repeat
    result = GgufSpeechToText("canary", "c.gguf").transcribe(np.zeros(16000, np.float32), "lt")
    assert result.text == "jono trečias"
    assert result.low_confidence_reason == "canary: output repetition"
    assert not result.failed


def test_gguf_error_is_a_failure(fake_transcribe_cpp):
    behaviour, _ = fake_transcribe_cpp

    def boom(audio, language):  # noqa: ARG001
        raise RuntimeError("backend error")

    behaviour["run"] = boom
    result = GgufSpeechToText("canary", "c.gguf").transcribe(np.zeros(16000, np.float32), "lt")
    assert result.text is None and result.failed


# ---------------------------------------------------------------------------
# Pipeline: two-model utterances
# ---------------------------------------------------------------------------
class _RecordingSender:
    def __init__(self):
        self.sent: list[Reference] = []

    def submit(self, ref: Reference) -> None:
        self.sent.append(ref)


class _RecordingTranscripts:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def submit(self, text: str, language: str) -> None:
        self.sent.append((text, language))


class _ScriptedSTT:
    language = "lt"

    def __init__(self, result: Transcription):
        self.result = result

    def transcribe_utterance(self, pcm, language=None):  # noqa: ARG002
        return self.result


def _two_model_processor(result: Transcription, tmp_path) -> BibleLiveAudioProcessor:
    proc = make_processor(
        config=AudioPipelineConfig(language="lt", stt_engine="kmynas+canary"),
        _stt_models=["kmynas", "canary"],
        stt=_ScriptedSTT(result),
        sender=_RecordingSender(),
        transcript_sender=_RecordingTranscripts(),
        bible_db_path=str(tmp_path / "missing.sqlite"),  # parse without validation
    )
    proc._init_thread_local_parsers()
    return proc


def _two_model_result(primary: str | None, alternate: str | None) -> Transcription:
    return Transcription(
        primary,
        2.0,
        source="kmynas",
        alternate=Transcription(alternate, 1.0, source="canary"),
    )


def test_two_model_parsers_apply_each_models_map(tmp_path):
    proc = _two_model_processor(_two_model_result(None, None), tmp_path)
    primary = proc._local.streaming_parsers["lt"]
    alternate = proc._local.alternate_parsers["lt"]
    assert isinstance(primary, MappedStreamingParser)
    assert isinstance(alternate, MappedStreamingParser)
    assert primary.spelling_map("Psmė") == "Psalmė"
    assert alternate.spelling_map("Salme") == "psalme"
    # English stays on Whisper: no map, no second parser.
    assert type(proc._local.streaming_parsers["en"]) is StreamingParser
    assert "en" not in proc._local.alternate_parsers


def test_reference_both_models_find_keeps_its_confidence(tmp_path):
    proc = _two_model_processor(
        _two_model_result("Psmė dvidešimt trečia, pirma eilutė", "Salme 23, 1 eilutė"), tmp_path
    )
    proc._process_utterance("lt", [ONE_SECOND])
    assert [(str(r), r.confidence) for r in proc.sender.sent] == [("Psalms 23:1", "high")]


def test_reference_one_model_finds_is_sent_as_low(tmp_path):
    proc = _two_model_processor(
        _two_model_result("Jono trečias skyrius šešiolikta eilutė", "Jono, kai jis kalbėjo"),
        tmp_path,
    )
    proc._process_utterance("lt", [ONE_SECOND])
    assert [(str(r), r.confidence) for r in proc.sender.sent] == [("John 3:16", "low")]


# Romans 8 named, then more than the parser's 12 carry tokens of talk, so the
# next utterance's bare verse is resolved from context, not joined to the name.
_TALK = ", ir šiandien mes kalbėsime apie Dievo meilę ir apie tai, kaip jis mus myli kasdien"
_ROMANS_8 = "Romiečiams aštuntas skyrius" + _TALK
_ROMANS_8_DIGITS = "Romiečiams 8 skyrius" + _TALK


def _context_then_verse(proc, verse: Transcription) -> None:
    proc._process_utterance("lt", [ONE_SECOND])  # "Romiečiams 8 ..." from both models
    proc.stt.result = verse
    proc.sender.sent.clear()
    proc._process_utterance("lt", [ONE_SECOND])


def test_context_verse_both_models_inferred_is_sent_as_medium(tmp_path):
    proc = _two_model_processor(_two_model_result(_ROMANS_8, _ROMANS_8_DIGITS), tmp_path)
    _context_then_verse(proc, _two_model_result("trisdešimta eilutė", "30 eilutė"))
    assert [(str(r), r.confidence) for r in proc.sender.sent] == [("Romans 8:30", "medium")]


def test_context_verse_from_a_shaky_decode_stays_low(tmp_path):
    # A repetition loop on either model is never promoted to auto-approve.
    proc = _two_model_processor(_two_model_result(_ROMANS_8, _ROMANS_8_DIGITS), tmp_path)
    shaky = Transcription(
        "30 eilutė", 1.0, source="canary", low_confidence_reason="canary: output repetition"
    )
    verse = Transcription("trisdešimta eilutė", 2.0, source="kmynas", alternate=shaky)
    _context_then_verse(proc, verse)
    assert [(str(r), r.confidence) for r in proc.sender.sent] == [("Romans 8:30", "low")]


def test_second_model_alone_still_reaches_the_operator_as_low(tmp_path):
    proc = _two_model_processor(_two_model_result(None, "Salme 23, 1 eilutė"), tmp_path)
    proc._process_utterance("lt", [ONE_SECOND])
    assert [(str(r), r.confidence) for r in proc.sender.sent] == [("Psalms 23:1", "low")]
    assert proc.transcript_sender.sent == [("Salme 23, 1 eilutė", "lt")]
    assert proc.metrics.transcription_failures == 0


def test_neither_model_hearing_anything_is_no_transcription(tmp_path):
    proc = _two_model_processor(_two_model_result(None, None), tmp_path)
    proc._process_utterance("lt", [ONE_SECOND])
    assert proc.sender.sent == []
    # Hearing nothing is not a failure (the metrics showed "3 failed" for blips).
    assert proc.metrics.transcription_failures == 0
    assert proc.metrics.transcriptions_empty == 1


def test_a_speech_model_error_counts_as_a_failure(tmp_path):
    proc = _two_model_processor(Transcription(None, 0.0, failed=True, source="kmynas"), tmp_path)
    proc._process_utterance("lt", [ONE_SECOND])
    assert proc.metrics.transcription_failures == 1
    assert proc.metrics.transcriptions_empty == 0


def test_primary_parser_error_still_sends_the_second_models_references(tmp_path):
    proc = _two_model_processor(
        _two_model_result("Jono trečias skyrius šešiolikta eilutė", "Salme 23, 1 eilutė"), tmp_path
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("parser bug")

    proc._local.streaming_parsers["lt"].feed = boom
    proc._process_utterance("lt", [ONE_SECOND])
    assert [(str(r), r.confidence) for r in proc.sender.sent] == [("Psalms 23:1", "low")]


@pytest.mark.parametrize(
    ("engine", "models", "expected"),
    [("kmynas", ["kmynas"], "en"), ("whisper", [], "lt")],
)
def test_whisper_starts_in_a_language_it_will_hear(monkeypatch, engine, models, expected):
    # Next to a Lithuanian GGUF engine Whisper only hears English: its load log
    # said "language=lt" on the installed CPU tier.
    created: list[str] = []
    monkeypatch.setattr("audio.pipeline.SpeechToText", lambda **kw: created.append(kw["language"]))
    proc = make_processor(
        config=AudioPipelineConfig(language="lt", stt_engine=engine), _stt_models=models
    )
    proc._create_whisper()
    assert created == [expected]
