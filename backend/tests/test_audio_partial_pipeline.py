"""Partial transcripts in the live pipeline: VAD hand-over, worker priority, isolation.

A partial feeds follow-along only. These tests check that it never reaches the
reference path, the session log or the transcription failure counters; that the
VAD thread hands over snapshots only while armed and drops them when the utterance
closes; and that a queued final is always transcribed before a waiting partial.

No Whisper model, ffmpeg, GPU or network: the processor is assembled from fakes,
as in test_audio_language_switch.py. The VAD loop runs on the test thread.
"""

import argparse
import logging
from queue import Queue
from types import SimpleNamespace

import pytest
from processor_factory import make_processor

from audio.cli import add_tuning_arguments, apply_tuning_arguments, build_parser
from audio.config import AudioPipelineConfig
from audio.metrics import PipelineMetrics
from audio.partials import PartialSnapshot, PartialTranscript
from audio.pipeline import BibleLiveAudioProcessor
from audio.segmenter import Segmenter
from audio.stt import SpeechToText, Transcription
from audio.stt_gguf import MultiModelSpeechToText

# The pipeline module imports faster-whisper (optional audio extras).
pytestmark = pytest.mark.audio

# 30 ms of 16 kHz 16-bit mono. VOICED is loud enough for frame_is_loud; SILENT is not.
VOICED = b"\x01" * 960
SILENT = b"\x00" * 960
# With these settings a partial is due every 10 frames (0.3 s) of utterance time, and
# the utterance closes after 4 silent frames.
INTERVAL_FRAMES = 10


def _config(**overrides) -> AudioPipelineConfig:
    values = {
        "language": "lt",
        "partial_transcripts": True,
        # Grow: any snapshot is transcribed, however short (chunk mode waits for 0.5 s
        # of new audio; its own tests say so).
        "partial_mode": "grow",
        "partial_interval_s": 0.3,
        "partial_min_s": 0.3,
        "end_of_speech_s": 0.09,
        "min_speech_s": 0.03,
    }
    values.update(overrides)
    return AudioPipelineConfig(**values)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _ArmableTranscripts:
    """Stands in for TranscriptSender: ``armed`` is set by the test; submits are kept."""

    def __init__(self, armed: bool = True):
        self._armed = armed
        self.reads = 0  # how often the pipeline asked
        self.sent: list[tuple[str, str]] = []

    @property
    def armed(self) -> bool:
        self.reads += 1
        return self._armed

    @armed.setter
    def armed(self, value: bool) -> None:
        self._armed = value

    def submit(self, text: str, language: str) -> None:
        self.sent.append((text, language))

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class _RecordingSender:
    """Stands in for the reference ReferenceSender."""

    def __init__(self):
        self.sent = []

    def submit(self, ref) -> None:
        self.sent.append(ref)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class _SpySessionLog:
    """Records every call, whatever the method: the session log must not be touched."""

    def __init__(self):
        self.calls: list[str] = []

    def __getattr__(self, name):
        def record(*args, **kwargs):  # noqa: ARG001 - signature parity
            self.calls.append(name)

        return record


class _ScriptedSTT:
    """Speech model stand-in: partial answers come from a list, one per call."""

    def __init__(self, partials: list[Transcription] | None = None):
        self.language = "lt"
        self.partials = list(partials or [])
        self.partial_calls: list[tuple[int, str]] = []  # (audio bytes, language)
        self.final_calls = 0

    def set_language(self, language: str) -> None:
        self.language = language

    def transcribe_utterance(self, pcm, language=None):  # noqa: ARG002 - signature parity
        self.final_calls += 1
        return Transcription(None, 0.0)

    def transcribe_partial(self, pcm, language):
        self.partial_calls.append((len(pcm), language))
        return self.partials.pop(0)


class _SpeechModel:
    """One GGUF model for MultiModelSpeechToText: records its calls, returns a fixed result."""

    def __init__(self, name: str, result: Transcription):
        self.name = name
        self.result = result
        self.calls: list[tuple[int, str]] = []

    def transcribe(self, audio, language):
        self.calls.append((len(audio), language))
        return self.result


class _Stream:
    """Stands in for AudioCapture: yields the frames, then stops the VAD loop."""

    def __init__(self, proc, frames):
        self._proc = proc
        self._frames = list(frames)
        self.failed = False
        self.audio_queue = SimpleNamespace(qsize=lambda: 0)

    def get_audio_frame(self, duration_ms):  # noqa: ARG002 - signature parity
        if self._frames:
            return self._frames.pop(0)
        self._proc.is_running = False
        return None


def _processor(config, stt=None, transcripts=None) -> BibleLiveAudioProcessor:
    """A processor assembled as __init__ does, minus audio capture and speech models."""
    return make_processor(
        config=config,
        is_running=True,
        stt=stt or _ScriptedSTT(),
        segmenter=Segmenter.from_config(lambda frame: frame == VOICED, config),
        vad_queue=Queue(maxsize=10),
        sender=_RecordingSender(),
        transcript_sender=transcripts or _ArmableTranscripts(),
        # Built even with partials off (the factory leaves it None then).
        _partial_transcript=PartialTranscript(config.partial_mode),
    )


def _run_vad(proc, frames) -> None:
    """Feed ``frames`` through the real VAD loop on this thread."""
    proc.audio_capture = _Stream(proc, frames)
    proc.is_running = True
    proc._vad_loop()


def _snapshot(utterance: int, frames: int, language: str = "lt") -> PartialSnapshot:
    return PartialSnapshot(utterance, language, tuple([VOICED] * frames))


def _snapshot_pcm_bytes(frames: int) -> int:
    return len(VOICED) * frames


# ---------------------------------------------------------------------------
# VAD thread: hand-over while armed, discard on close
# ---------------------------------------------------------------------------
def test_the_mailbox_holds_the_newest_snapshot_of_the_open_utterance():
    proc = _processor(_config())
    _run_vad(proc, [VOICED] * 25)

    snapshot = proc._partial_mailbox.take()

    # Due at 10 and 20 frames; the 20-frame snapshot replaced the 10-frame one.
    assert snapshot.utterance == 1
    assert snapshot.language == "lt"
    assert len(snapshot.frames) == 2 * INTERVAL_FRAMES


def test_snapshots_are_handed_over_only_while_armed():
    transcripts = _ArmableTranscripts(armed=False)
    proc = _processor(_config(), transcripts=transcripts)

    _run_vad(proc, [VOICED] * 25)
    assert proc._partial_mailbox.take() is None

    transcripts.armed = True
    _run_vad(proc, [VOICED] * 5)  # frames 26 to 30
    snapshot = proc._partial_mailbox.take()
    assert snapshot is not None
    assert len(snapshot.frames) == 3 * INTERVAL_FRAMES


def test_the_schedule_keeps_its_timing_while_disarmed():
    """Armed mid-utterance: the next partial is at the next multiple, not a stale one."""
    transcripts = _ArmableTranscripts(armed=False)
    proc = _processor(_config(), transcripts=transcripts)
    _run_vad(proc, [VOICED] * 25)  # 10 and 20 pass while disarmed

    transcripts.armed = True
    _run_vad(proc, [VOICED] * 4)  # frames 26 to 29: nothing due yet
    assert proc._partial_mailbox.take() is None

    _run_vad(proc, [VOICED])  # frame 30
    assert len(proc._partial_mailbox.take().frames) == 3 * INTERVAL_FRAMES


def test_closing_the_utterance_discards_its_snapshot_and_queues_the_final():
    proc = _processor(_config(), transcripts=_ArmableTranscripts())

    _run_vad(proc, [VOICED] * 25 + [SILENT] * 4)

    assert proc._partial_mailbox.take() is None
    language, frames = proc.vad_queue.get_nowait()
    assert language == "lt"
    assert len(frames) == 25


def test_a_blip_discards_its_snapshot_too():
    """A too-short utterance is not queued, but its partial must still go."""
    proc = _processor(_config(min_speech_s=0.6), transcripts=_ArmableTranscripts())

    _run_vad(proc, [VOICED] * 12 + [SILENT] * 4)

    assert proc._partial_mailbox.take() is None
    assert proc.vad_queue.empty()


def test_a_language_switch_discards_the_open_utterances_snapshot():
    proc = _processor(_config(), transcripts=_ArmableTranscripts())
    _run_vad(proc, [VOICED] * 25)
    proc.set_language("en")

    proc._consume_pending_language_switch()

    assert proc._partial_mailbox.take() is None


def test_restarting_capture_discards_the_flushed_utterances_snapshot():
    proc = _processor(_config(), transcripts=_ArmableTranscripts())
    _run_vad(proc, [VOICED] * 25)
    proc.is_running = False  # _restart_capture returns straight after its flush

    proc._restart_capture()

    assert proc._partial_mailbox.take() is None
    _, frames = proc.vad_queue.get_nowait()  # the flushed utterance is still queued
    assert len(frames) == 25


def test_partials_off_means_the_partial_path_is_never_taken():
    """--partial-transcripts off: no snapshot, and the transcript sender's flag is never read."""
    transcripts = _ArmableTranscripts(armed=True)
    proc = _processor(_config(partial_transcripts=False), transcripts=transcripts)

    _run_vad(proc, [VOICED] * 25 + [SILENT] * 4)

    assert transcripts.reads == 0
    assert proc._partial_mailbox.take() is None
    assert proc.vad_queue.qsize() == 1  # the final, exactly as before


class _NoWhisperModel:
    """Stands in for SpeechToText: the constructor test needs no model, and loads none."""

    def __init__(self, *args, **kwargs):  # noqa: ARG002 - signature parity
        pass


def test_a_bad_partial_interval_stops_startup_only_when_partials_are_on(monkeypatch):
    """With partials off the interval is never read, so a bad value cannot stop startup."""
    monkeypatch.setattr("audio.pipeline.SpeechToText", _NoWhisperModel)

    proc = BibleLiveAudioProcessor(
        config=AudioPipelineConfig(partial_transcripts=False, partial_interval_s=0.0)
    )
    assert proc._partial_schedule is None

    with pytest.raises(ValueError, match="interval_frames"):
        BibleLiveAudioProcessor(
            config=AudioPipelineConfig(partial_transcripts=True, partial_interval_s=0.0)
        )


# ---------------------------------------------------------------------------
# Transcription thread: finals first, partial path
# ---------------------------------------------------------------------------
def test_a_queued_final_is_transcribed_before_a_waiting_partial():
    order: list[str] = []
    proc = _processor(_config(), transcripts=_ArmableTranscripts())

    class _OrderSTT:
        language = "lt"

        def transcribe_utterance(self, pcm, language=None):  # noqa: ARG002
            order.append("final")
            return Transcription(None, 0.0)

        def transcribe_partial(self, pcm, language):  # noqa: ARG002
            order.append("partial")
            proc.is_running = False  # one partial is enough for this test
            return Transcription(None, 0.0)

    proc.stt = _OrderSTT()
    proc._init_thread_local_parsers = lambda: None  # no Bible database needed here
    proc.vad_queue.put(("lt", [VOICED] * 20))
    proc._partial_mailbox.put(_snapshot(1, INTERVAL_FRAMES))

    proc._transcription_loop()

    assert order == ["final", "partial"]


def test_a_partial_that_raises_is_dropped_and_the_worker_goes_on():
    """A failing partial is counted as dropped; the transcription thread keeps running."""

    class _FlakySTT:
        language = "lt"

        def __init__(self):
            self.partial_calls = 0

        def transcribe_partial(self, pcm, language):  # noqa: ARG002
            self.partial_calls += 1
            if self.partial_calls == 1:
                # A newer snapshot is waiting when the first one fails.
                proc._partial_mailbox.put(_snapshot(1, 2 * INTERVAL_FRAMES))
                raise RuntimeError("model fell over")
            proc.is_running = False  # the second partial is enough for this test
            return Transcription("still here", 1.0)

    proc = _processor(_config(), transcripts=_ArmableTranscripts())
    stt = _FlakySTT()
    proc.stt = stt
    proc._init_thread_local_parsers = lambda: None  # no Bible database needed here
    proc._partial_mailbox.put(_snapshot(1, INTERVAL_FRAMES))

    proc._transcription_loop()  # returns normally: the error did not end the thread

    assert stt.partial_calls == 2
    assert proc.transcript_sender.sent == [("still here", "lt")]
    assert proc.metrics.partials_dropped == 1
    # Not a speech-model failure: no transcription failure is counted for it.
    assert proc.metrics.transcription_failures == 0
    assert proc._consecutive_transcription_errors == 0


def test_a_partial_uses_the_primary_model_alone_and_reaches_only_follow_along():
    kmynas = _SpeechModel("kmynas", Transcription("Romans 8:28", 2.0))
    canary = _SpeechModel("canary", Transcription("Romans 8:28", 2.0))
    stt = MultiModelSpeechToText([kmynas, canary], whisper_factory=_no_whisper, language="lt")
    transcripts = _ArmableTranscripts()
    proc = _processor(_config(stt_engine="kmynas+canary"), stt=stt, transcripts=transcripts)
    proc.session_log = _SpySessionLog()
    # A partial is never parsed or detected: any path into those fails the test.
    proc._select_parser = _must_not_run
    proc._detect_references = _must_not_run
    proc._process_utterance = _must_not_run

    proc._process_partial(_snapshot(1, 20))

    assert kmynas.calls == [(20 * 480, "lt")]  # 20 frames, as float32 samples
    assert canary.calls == []
    assert transcripts.sent == [("Romans 8:28", "lt")]
    assert proc.sender.sent == []  # the reference sender never sees it
    assert proc.session_log.calls == []
    m = proc.metrics
    assert m.partials_transcribed == 1
    assert m.transcription_attempts == m.transcription_successes == 0
    assert m.transcription_failures == m.transcriptions_empty == m.references_detected == 0
    assert proc._consecutive_transcription_errors == 0


@pytest.mark.parametrize(
    "result",
    [
        Transcription("Romans 8:28", 1.0, low_confidence_reason="avg_logprob -1.4 < -1.0"),
        Transcription(None, 1.0, failed=True),
    ],
    ids=["low-confidence", "failed"],
)
def test_a_partial_that_is_not_confident_or_fails_is_dropped_quietly(result):
    transcripts = _ArmableTranscripts()
    proc = _processor(_config(), stt=_ScriptedSTT([result]), transcripts=transcripts)

    proc._process_partial(_snapshot(1, 20))

    assert transcripts.sent == []
    assert proc.metrics.partials_transcribed == 1
    assert proc.metrics.partials_dropped == 1
    # No transcription error counter, no failure metric, no health issue.
    assert proc.metrics.transcription_failures == 0
    assert proc._consecutive_transcription_errors == 0
    assert proc.sender.sent == []


def test_grow_sends_the_whole_utterance_so_far():
    stt = _ScriptedSTT([Transcription("one two", 1.0), Transcription("one two three", 1.0)])
    transcripts = _ArmableTranscripts()
    proc = _processor(_config(partial_mode="grow"), stt=stt, transcripts=transcripts)

    proc._process_partial(_snapshot(1, 20))
    proc._process_partial(_snapshot(1, 40))

    assert stt.partial_calls == [(_snapshot_pcm_bytes(20), "lt"), (_snapshot_pcm_bytes(40), "lt")]
    assert transcripts.sent == [("one two", "lt"), ("one two three", "lt")]
    assert proc.metrics.partials_transcribed == 2
    # 20 frames is 0.6 s and 40 frames 1.2 s of audio.
    assert proc.metrics.partial_audio_seconds == pytest.approx(0.6 + 1.2)


def test_chunk_sends_only_the_new_audio_and_joins_the_texts():
    stt = _ScriptedSTT([Transcription("one two", 1.0), Transcription("three four", 1.0)])
    transcripts = _ArmableTranscripts()
    proc = _processor(_config(partial_mode="chunk"), stt=stt, transcripts=transcripts)

    proc._process_partial(_snapshot(1, 20))
    proc._process_partial(_snapshot(1, 40))

    assert stt.partial_calls == [(_snapshot_pcm_bytes(20), "lt"), (_snapshot_pcm_bytes(20), "lt")]
    assert transcripts.sent == [("one two", "lt"), ("one two three four", "lt")]


def test_a_partial_is_skipped_when_follow_along_disarmed_after_the_snapshot():
    stt = _ScriptedSTT([Transcription("one", 1.0)])
    transcripts = _ArmableTranscripts(armed=False)
    proc = _processor(_config(), stt=stt, transcripts=transcripts)

    proc._process_partial(_snapshot(1, 20))

    assert stt.partial_calls == []
    assert transcripts.sent == []
    assert proc.metrics.partials_transcribed == 0


def test_partials_metrics_are_in_the_summary(caplog):
    metrics = PipelineMetrics(
        partials_transcribed=3,
        partial_audio_seconds=4.5,
        partial_transcription_ms=900.0,
        partials_dropped=1,
    )

    with caplog.at_level(logging.INFO, logger="audio.metrics"):
        metrics.log_summary()

    assert "Partial transcripts: 3 (4.5s audio, 0.9s to transcribe, 1 dropped)" in caplog.text


# ---------------------------------------------------------------------------
# Speech models: the primary alone, and never a wait for Whisper
# ---------------------------------------------------------------------------
def _no_whisper():
    raise AssertionError("Whisper must not be loaded for a partial")


def _must_not_run(*args, **kwargs):  # noqa: ARG001 - signature parity
    raise AssertionError("a partial must not reach the parser or the detection path")


def test_a_partial_in_english_never_loads_or_runs_whisper():
    """Whisper takes no partials: skipped before a load, after one, and after a failure."""
    loads: list[str] = []
    stt = MultiModelSpeechToText(
        [_SpeechModel("kmynas", Transcription("x", 1.0))],
        whisper_factory=lambda: loads.append("load"),
        language="lt",
    )

    assert stt.transcribe_partial(bytes(32000), "en") is None
    assert loads == []
    assert stt._whisper_loader is None

    class _LoadedWhisper:
        def transcribe_utterance(self, pcm, language=None):  # noqa: ARG002
            raise AssertionError("Whisper must not transcribe a partial")

    stt._whisper = _LoadedWhisper()
    assert stt.transcribe_partial(bytes(32000), "en") is None

    stt._whisper, stt._whisper_failed = None, True
    assert stt.transcribe_partial(bytes(32000), "en") is None


def test_whisper_alone_takes_no_partials():
    """--stt whisper (and English): the speech model is never asked for a partial."""
    whisper = SpeechToText.__new__(SpeechToText)  # no model loaded; none is needed

    assert whisper.transcribe_partial(bytes(32000), "en") is None


def test_a_short_lithuanian_partial_is_not_sent_to_the_model():
    kmynas = _SpeechModel("kmynas", Transcription("x", 1.0))
    stt = MultiModelSpeechToText([kmynas], whisper_factory=_no_whisper, language="lt")

    result = stt.transcribe_partial(bytes(8000), "lt")  # 0.25 s: under the 0.5 s minimum

    assert result is not None and result.text is None
    assert kmynas.calls == []


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------
def test_partial_flags_reach_the_config():
    args = build_parser().parse_args(
        [
            "--partial-transcripts",
            "on",
            "--partial-mode",
            "chunk",
            "--partial-interval-s",
            "3",
            "--partial-min-s",
            "1",
        ]
    )
    config = AudioPipelineConfig()

    apply_tuning_arguments(config, args)

    assert config.partial_transcripts is True
    assert config.partial_mode == "chunk"
    assert config.partial_interval_s == 3.0
    assert config.partial_min_s == 1.0


def test_partial_flags_default_to_the_config_defaults():
    config = AudioPipelineConfig()

    apply_tuning_arguments(config, build_parser().parse_args([]))

    assert config == AudioPipelineConfig()


def test_an_unknown_partial_mode_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--partial-mode", "fast"])


def test_the_tuning_flags_alone_do_not_take_the_partial_flags():
    """run_benchmark.py adds only the tuning flags, so a partial flag is an unknown argument."""
    parser = argparse.ArgumentParser()
    add_tuning_arguments(parser)

    assert parser.parse_args(["--beam-size", "3"]).beam_size == 3
    with pytest.raises(SystemExit):
        parser.parse_args(["--partial-transcripts", "on"])
