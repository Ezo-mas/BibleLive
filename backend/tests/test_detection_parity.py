"""The benchmark must detect exactly what the live pipeline detects.

The benchmark is the acceptance gate for every detection change, so the way it
builds parsers, collapses duplicates, marks a shaky decode "low", starts Whisper
and decodes must stay the live pipeline's. These tests drive both entry points
on the same input and compare what comes out.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from processor_factory import make_processor

import benchmarks.run_benchmark as bench
from audio import pipeline as pipeline_module
from audio.config import AudioPipelineConfig
from audio.detector import UtteranceDetector
from audio.metrics import PipelineMetrics
from audio.stt import SpeechToText, Transcription, _language_state
from benchmarks.session_replay import Replayer
from core.verse_parser import Reference
from core.verse_text import VerseTextIndex

ENGINES = ["whisper", "kmynas", "canary", "kmynas+canary"]
LANGUAGES = ["en", "lt"]

# A chapter-only reference the parser holds one utterance, then releases.
FEEDS = {
    "en": [(0.0, "Turn with me to Romans chapter five"), (20.0, "and we read on")],
    "lt": [(0.0, "Atsiverskime Romiečiams 5 skyrius"), (20.0, "ir skaitome toliau")],
}

VERSE_TEXT = VerseTextIndex([("Romans", 5, v, "teisinami tikėjimu turime ramybę") for v in (1, 2)])


def _live_parsers(engine: str, monkeypatch) -> UtteranceDetector:
    """The detector the live transcription thread builds for ``engine``."""
    processor = make_processor(config=AudioPipelineConfig(stt_engine=engine))
    processor._local.bible_lookup = None  # no validator, as in the benchmark below
    monkeypatch.setattr(UtteranceDetector, "load_verse_text", staticmethod(lambda path: VERSE_TEXT))
    processor._init_thread_local_parsers()
    return processor._local.detector


def _spelled(parser) -> str:
    """A text with Kmynas and Canary misspellings, as ``parser`` rewrites it."""
    rewrite = getattr(parser, "spelling_map", None) or (lambda text: text)
    return rewrite("Psmė Salme 17.17 Gavatams")


def _run(parser, feeds) -> list[tuple]:
    refs = []
    for t_s, text in feeds:
        refs += parser.feed(text, t_s)
    refs += parser.flush()
    return [(r.book, r.chapter, r.verse_start, r.verse_end, r.confidence) for r in refs]


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("engine", ENGINES)
def test_benchmark_builds_the_live_parsers(engine, language, monkeypatch):
    monkeypatch.setattr(bench, "_VALIDATOR", None)
    monkeypatch.setattr(bench, "_VERSE_TEXT", VERSE_TEXT)
    live = _live_parsers(engine, monkeypatch)
    config = AudioPipelineConfig(stt_engine=engine, language=language)
    # The benchmark skill runs with --context-ttl-s 600, live's default.
    benchmark, alternate = bench.make_parsers(config, language, 600.0)
    assert isinstance(live, UtteranceDetector)
    assert isinstance(benchmark, UtteranceDetector)
    primary = benchmark.streaming_parsers[language]
    pairs = [(live.streaming_parsers[language], primary)]
    assert (language in live.alternate_parsers) == (alternate is not None)
    if alternate is not None:
        pairs.append((live.alternate_parsers[language], alternate))
    for live_parser, bench_parser in pairs:
        assert type(bench_parser) is type(live_parser)
        assert bench_parser.hold_chapter_only == live_parser.hold_chapter_only
        assert bench_parser.context_ttl_s == live_parser.context_ttl_s
        assert bench_parser.verse_text is live_parser.verse_text
        assert bench_parser.parser.language == live_parser.parser.language
        # Each model's own spelling map (Whisper's text passes through).
        assert _spelled(bench_parser) == _spelled(live_parser)
        assert _run(bench_parser, FEEDS[language]) == _run(live_parser, FEEDS[language])


class _RepeatingParser:
    """A decoder loop's output: the same references several times over."""

    def feed(self, text, t_s):  # noqa: ARG002
        return [
            Reference("John", 3, 16),
            Reference("Romans", 8, 28, confidence="medium"),
            Reference("John", 3, 16),
            Reference("John", 3, 16, 17),
            Reference("Romans", 8, 28, confidence="medium"),
        ]


@pytest.mark.parametrize("low_reason", [None, "avg_logprob -1.20 < -1.0"])
def test_duplicates_and_low_confidence_match_live(low_reason):
    result = Transcription("Jono trys šešiolika", 0.0, low_confidence_reason=low_reason)
    submitted = []
    live = make_processor(
        stt=SimpleNamespace(transcribe_utterance=lambda *args: result),
        sender=SimpleNamespace(submit=submitted.append),
        transcript_sender=SimpleNamespace(submit=lambda *args: None),
    )
    live._local.bible_lookup = None
    live._init_thread_local_parsers()
    live._local.detector.streaming_parsers["en"].feed = _RepeatingParser().feed
    benchmark, _ = bench.make_parsers(live.config, "en", 600.0)
    benchmark.streaming_parsers["en"].feed = _RepeatingParser().feed
    benchmark.metrics = PipelineMetrics()
    detections = []
    for now in (0.0, 20.0):
        live._process_utterance("en", [b""])
        bench.register_feeds(
            benchmark, None, [(now, result.text, now, low_reason or "")], detections
        )

    def coords(refs):
        return [(r.book, r.chapter, r.verse_start, r.verse_end, r.confidence) for r in refs]

    expected = [
        ("John", 3, 16, None, "low" if low_reason else "high"),
        ("Romans", 8, 28, None, "low" if low_reason else "medium"),
        ("John", 3, 16, 17, "low" if low_reason else "high"),
    ] * 2
    assert coords(submitted) == coords(detections) == expected
    assert live.metrics.references_detected == 6
    # Group-level dedupe cannot hide a missing per-utterance collapse here:
    # live submissions are captured before sender dedupe, and benchmark's
    # per-model counters are checked before its group-level filter.
    for metrics in (live.metrics, benchmark.metrics):
        assert metrics.references_deduplicated == 4
        assert metrics.references_low_confidence == (6 if low_reason else 0)


@pytest.mark.parametrize("two_models", [False, True])
def test_benchmark_detector_controls_its_alternate(two_models, monkeypatch):
    config = AudioPipelineConfig(stt_engine="kmynas+canary" if two_models else "whisper")
    detector, alternate = bench.make_parsers(config, "lt", 600.0)
    foreign = UtteranceDetector([]).streaming_parsers["lt"]

    def must_not_feed(*args):
        raise AssertionError("the separate alternate must not override the detector")

    foreign.feed = must_not_feed
    detections = []
    feed = (20.0, "Romiečiams 5 skyrius", 12.0, "", "Romiečiams 5 skyrius", "")
    bench.register_feeds(detector, None, [feed], detections, alternate_parser=foreign)
    assert detections == []
    register = bench.register_feeds

    def check_release(parser, sender, feeds, detections, spans, alternate_parser):
        assert alternate_parser is alternate
        assert len(feeds[0]) == (6 if two_models else 2)
        return register(parser, sender, feeds, detections, spans, alternate_parser)

    monkeypatch.setattr(bench, "register_feeds", check_release)
    bench.release_held(detector, None, [[feed]], detections, alternate_parser=foreign)
    assert [(r.book, r.chapter, r.verse_start) for r in detections] == [("Romans", 5, None)]
    assert detections[0].confidence != "low"


class _RecordingModel:
    def __init__(self):
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):  # noqa: ARG002
        self.calls.append(kwargs)
        return iter(()), SimpleNamespace()


@pytest.mark.parametrize("language", LANGUAGES)
def test_windowed_mode_decodes_with_the_live_whisper_settings(language):
    stt = SpeechToText.__new__(SpeechToText)
    stt.model = _RecordingModel()
    stt.beam_size = 3
    stt.sample_rate = 16000
    stt._state = _language_state(language)

    stt.transcribe_utterance(np.zeros(16000, dtype=np.int16).tobytes(), language)
    bench._transcribe_window_segments(stt, np.zeros(16000, dtype=np.float32), language)

    live, windowed = stt.model.calls
    assert windowed == live


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("engine", ENGINES)
def test_benchmark_starts_whisper_like_live(engine, language, monkeypatch):
    started: list[dict] = []

    class FakeWhisper:
        def __init__(self, **kwargs):
            started.append(kwargs)

    def fake_gguf(engine, model_paths, device, cpu_threads, language, whisper_factory):  # noqa: ARG001
        return whisper_factory()

    for module in (pipeline_module, bench):
        monkeypatch.setattr(module, "SpeechToText", FakeWhisper)
        monkeypatch.setattr(module, "create_gguf_speech_to_text", fake_gguf)

    config = AudioPipelineConfig(stt_engine=engine, language=language, beam_size=3)
    processor = make_processor(config=config)
    processor._create_whisper()
    bench.make_stt(config, language)

    live, benchmark = started
    if engine != "whisper" and language == "lt":
        # Next to GGUF models Whisper only hears the languages they cannot.
        assert live["language"] == "en"
    assert benchmark == live


def test_session_replay_flushes_on_a_language_switch_like_live():
    held_lt, then_en = FEEDS["lt"][0][1], FEEDS["en"][1][1]

    submitted: list = []
    processor = make_processor(sender=SimpleNamespace(submit=submitted.append))
    processor._local.detector = UtteranceDetector([])
    assert processor._select_parser("lt").feed(held_lt, 0.0) == []  # held
    processor._select_parser("en").feed(then_en, 20.0)

    replayer = Replayer()
    replayer.feed(
        {"language": "lt", "start_s": 0.0, "transcripts": [{"model": "whisper", "text": held_lt}]}
    )
    flushed, _ = replayer.feed(
        {"language": "en", "start_s": 20.0, "transcripts": [{"model": "whisper", "text": then_en}]}
    )

    assert [str(r) for r in flushed] == [str(r) for r in submitted] == ["Romans 5"]
