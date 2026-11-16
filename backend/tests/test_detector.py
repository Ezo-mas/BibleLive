"""The detection state can be built and driven without an audio pipeline."""

from types import SimpleNamespace

import pytest
from processor_factory import make_processor

from audio.detector import UtteranceDetector
from audio.metrics import PipelineMetrics
from audio.stt import Transcription
from core.verse_parser import Reference
from core.verse_text import VerseTextIndex


def _labels(refs):
    return [(str(ref), ref.confidence, ref.inferred) for ref in refs]


@pytest.mark.parametrize(
    ("language", "text"),
    [("en", "John 3:16"), ("lt", "Jono trečias skyrius šešiolikta eilutė")],
)
def test_both_languages(language, text):
    detector = UtteranceDetector([])
    result = detector.detect(Transcription(text, 0.0), language, 0.0)
    assert result.released == []
    assert _labels(result.references) == [("John 3:16", "high", False)]


def test_verse_text_is_only_given_to_lithuanian():
    index = VerseTextIndex([("John", 3, 16, "Dievas taip pamilo pasaulį")])
    detector = UtteranceDetector([], verse_text=index)
    assert detector.verse_text is index
    assert detector.streaming_parsers["lt"].verse_text is index
    assert detector.streaming_parsers["en"].verse_text is None


def test_receives_lookup_and_uses_its_validator():
    validator = lambda ref: False  # noqa: E731
    lookup = SimpleNamespace(conn=True, get_validator_hook=lambda: validator)
    detector = UtteranceDetector([], bible_lookup=lookup)
    assert detector.bible_lookup is lookup
    assert detector.validator is validator
    assert detector.issues == []
    assert detector.detect(Transcription("John 3:16", 0.0), "en", 0.0).references == []


def test_switch_returns_held_reference_before_utterance_references():
    detector = UtteranceDetector([])
    held = detector.detect(Transcription("Romans chapter five", 0.0), "en", 0.0)
    assert held.references == []
    switched = detector.detect(Transcription("Jono 3:16", 0.0), "lt", 20.0)
    assert [str(r) for r in switched.released] == ["Romans 5"]
    assert [str(r) for r in switched.references] == ["John 3:16"]
    assert detector.flush_held("en") == []
    detector.detect(Transcription("and we read on", 0.0), "en", 40.0)
    assert detector.streaming_parsers["en"].ctx.book is None


def test_shutdown_flush_merges_both_models_once():
    detector = UtteranceDetector(["kmynas", "canary"])
    transcription = Transcription(
        "Romiečiams 5 skyrius",
        0.0,
        alternate=Transcription("Romiečiams 5 skyrius", 0.0),
    )
    assert detector.detect(transcription, "lt", 0.0).references == []
    refs = detector.flush_held("lt")
    assert [str(r) for r in refs] == ["Romans 5"]
    assert refs[0].confidence != "low"
    assert detector.flush_held("lt") == []


@pytest.mark.parametrize(("when", "expected"), [(10.0, ["John 3:16"]), (601.0, ["John 3"])])
def test_context_expires_on_the_supplied_clock(when, expected):
    detector = UtteranceDetector([])
    detector.detect(Transcription("atsiverskime Jono evangeliją trečią skyrių", 0.0), "lt", 0.0)
    result = detector.detect(Transcription("šešiolikta eilutė sako", 0.0), "lt", when)
    assert [str(r) for r in result.references] == expected


@pytest.mark.parametrize(("shaky", "expected"), [(None, "medium"), ("output repetition", "low")])
def test_two_model_agreement_only_promotes_trusted_inference(shaky, expected):
    detector = UtteranceDetector(["kmynas", "canary"])
    talk = ", ir šiandien mes kalbėsime apie Dievo meilę ir apie tai, kaip jis mus myli kasdien"
    detector.detect(
        Transcription(
            "Romiečiams aštuntas skyrius" + talk,
            0.0,
            alternate=Transcription("Romiečiams 8 skyrius" + talk, 0.0),
        ),
        "lt",
        0.0,
    )
    refs = detector.detect(
        Transcription(
            "trisdešimta eilutė",
            0.0,
            alternate=Transcription("30 eilutė", 0.0, low_confidence_reason=shaky),
        ),
        "lt",
        20.0,
    ).references
    assert (str(refs[-1]), refs[-1].confidence) == ("Romans 8:30", expected)


@pytest.mark.parametrize("reason", [None, "avg_logprob -1.20 < -1.0"])
def test_duplicate_collapse_and_low_metrics(reason, caplog):
    metrics = PipelineMetrics()
    detector = UtteranceDetector([], metrics=metrics)
    detector.select_parser("en")
    detector.streaming_parsers["en"].feed = lambda *args: [
        Reference("John", 3, 16),
        Reference("John", 3, 16),
        Reference("Romans", 8, 28),
    ]
    refs = detector.detect(
        Transcription("text", 0.0, low_confidence_reason=reason), "en", 0.0
    ).references
    assert [str(r) for r in refs] == ["John 3:16", "Romans 8:28"]
    assert [r.confidence for r in refs] == ["low" if reason else "high"] * 2
    assert metrics.references_deduplicated == 1
    assert metrics.references_low_confidence == (2 if reason else 0)
    messages = ["Collapsed 1 duplicate reference(s) within this utterance"]
    if reason:
        messages.append(
            f"Low-confidence transcription ({reason}): marking 2 reference(s) as low confidence"
        )
    assert caplog.messages == messages


@pytest.mark.parametrize("catch_errors", [True, False])
@pytest.mark.parametrize("two_models", [True, False])
def test_parser_error_policy(catch_errors, two_models, caplog):
    detector = UtteranceDetector(
        ["kmynas", "canary"] if two_models else [], catch_parser_errors=catch_errors
    )
    detector.select_parser("lt")

    def boom(*args):
        raise RuntimeError("parser bug")

    detector.streaming_parsers["lt"].feed = boom
    result = Transcription(
        "Jono 3:16", 0.0, alternate=Transcription("Salme 23, 1 eilutė", 0.0) if two_models else None
    )
    if not catch_errors:
        with pytest.raises(RuntimeError, match="parser bug"):
            detector.detect(result, "lt", 0.0)
        assert caplog.messages == []
    else:
        refs = detector.detect(result, "lt", 0.0).references
        assert _labels(refs) == [("Psalms 23:1", "low", False)] if two_models else refs is None
        assert caplog.messages == ["Error parsing references: parser bug"]


def test_validator_swap_keeps_all_parser_state():
    detector = UtteranceDetector(["kmynas", "canary"], validator=lambda ref: True)
    detector.detect(Transcription("Romans chapter five", 0.0), "en", 0.0)
    parsers = [*detector.streaming_parsers.values(), *detector.alternate_parsers.values()]
    contexts = [p.ctx for p in parsers]
    validator = lambda ref: False  # noqa: E731
    detector.set_validator(validator)
    assert all(p.parser.validator is validator for p in parsers)
    assert all(p.ctx is ctx for p, ctx in zip(parsers, contexts, strict=True))
    assert [str(r) for r in detector.flush_held("en")] == ["Romans 5"]
    assert detector.detect(Transcription("John 3:16", 0.0), "en", 1.0).references == []


def test_live_transcript_switch_delivery_logs_and_parse_order(caplog):
    events = []
    proc = make_processor(
        stt=SimpleNamespace(transcribe_utterance=lambda *args: Transcription("Jono 3:16", 0.0)),
        sender=SimpleNamespace(submit=lambda ref: events.append(str(ref))),
        transcript_sender=SimpleNamespace(submit=lambda *args: events.append("transcript")),
    )
    detector = UtteranceDetector([], metrics=proc.metrics)
    proc._local.detector = detector
    detector.detect(Transcription("Romans chapter five", 0.0), "en", 0.0)
    parse = detector.streaming_parsers["lt"].feed

    def recording_feed(*args):
        events.append("parse")
        return parse(*args)

    detector.streaming_parsers["lt"].feed = recording_feed
    with caplog.at_level("INFO", logger="audio.pipeline"):
        proc._detect_references("lt", [b""])
    assert events == ["transcript", "Romans 5", "parse", "John 3:16"]
    assert caplog.messages[-4:] == [
        "Flushing held reference on language switch: Romans 5",
        "Selected 'lt' parser (context reset)",
        "Found 1 Bible reference(s):",
        "  - John 3:16 [high]",
    ]


def test_failed_switch_delivery_does_not_record_new_language():
    def fail(ref):
        raise RuntimeError("sender bug")

    proc = make_processor(sender=SimpleNamespace(submit=fail))
    detector = UtteranceDetector([])
    proc._local.detector = detector
    detector.detect(Transcription("Romans chapter five", 0.0), "en", 0.0)
    with pytest.raises(RuntimeError, match="sender bug"):
        proc._select_parser("lt")
    assert detector.last_parser_language == "en"
