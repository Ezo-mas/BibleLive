"""Tests for the live session log (``--session-log-dir``, ``--session-audio``)."""

import json
import threading
import wave

import pytest

from audio.cli import build_parser
from audio.config import AudioPipelineConfig
from audio.metrics import PipelineMetrics
from audio.pipeline import BibleLiveAudioProcessor
from audio.session_log import SessionLog
from audio.stt import Transcription
from core.verse_parser import Reference

# audio.stt imports faster-whisper (optional audio extras).
pytestmark = pytest.mark.audio

FRAME = b"\x01\x00" * 480  # 30 ms at 16 kHz


def _records(log: SessionLog) -> list[dict]:
    return [json.loads(line) for line in log.transcript_path.read_text("utf-8").splitlines()]


def test_header_names_the_session_setup(tmp_path):
    log = SessionLog(tmp_path, False, {"stt_engine": "kmynas"}, 0.03)
    log.close()
    (header,) = _records(log)
    assert header["type"] == "session"
    assert header["stt_engine"] == "kmynas"
    assert header["audio_file"] is None
    assert log.audio_path is None


def test_utterance_times_are_positions_in_the_recording(tmp_path):
    log = SessionLog(tmp_path, True, {}, 0.03)
    for _ in range(100):  # 3.0 s of audio
        log.write_frame(FRAME)
    log.speech_started(onset_frames=10)  # speech began 0.3 s before the latest frame
    for _ in range(50):
        log.write_frame(FRAME)
    frames = [FRAME] * 40
    log.utterance_queued(frames, trailing_silent_frames=5)
    ref = Reference(book="John", chapter=3, verse_start=16, confidence="low")
    result = Transcription(
        "Jono 3 16",
        12.0,
        source="kmynas",
        alternate=Transcription("Jono", 8.0, source="canary"),
    )
    log.utterance(frames, "lt", result, [ref])
    log.close()

    utterance = _records(log)[1]
    assert (utterance["start_s"], utterance["end_s"]) == (2.7, 4.35)
    assert [t["model"] for t in utterance["transcripts"]] == ["kmynas", "canary"]
    assert utterance["references"][0]["reference"] == "John 3:16"
    assert utterance["references"][0]["confidence"] == "low"
    assert log.audio_path is not None
    with wave.open(str(log.audio_path)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getnframes()) == (16000, 1, 150 * 480)


def test_unqueued_utterance_is_still_logged(tmp_path):
    log = SessionLog(tmp_path, False, {}, 0.03)
    log.utterance([FRAME], "en", None, [])
    log.close()
    utterance = _records(log)[1]
    assert utterance["start_s"] is None and utterance["transcripts"] == []


def test_events_and_flushed_references(tmp_path):
    log = SessionLog(tmp_path, False, {}, 0.03)
    log.event("language", language="lt", dropped_frames=0)
    log.references("shutdown_flush", [])  # nothing flushed: nothing logged
    log.references("shutdown_flush", [Reference(book="Romans", chapter=5)])
    log.close()
    kinds = [r["type"] for r in _records(log)]
    assert kinds == ["session", "language", "shutdown_flush"]


def test_write_error_turns_logging_off_without_raising(tmp_path):
    log = SessionLog(tmp_path, False, {}, 0.03)
    log._file.close()  # any later write fails
    log.event("language", language="en")
    log.utterance([FRAME], "en", None, [])
    assert not log._enabled
    log.close()


def test_cli_session_flags_reach_the_config():
    args = build_parser().parse_args(["--session-log-dir", "logs", "--session-audio", "on"])
    assert (args.session_log_dir, args.session_audio) == ("logs", True)
    defaults = build_parser().parse_args([])
    assert (defaults.session_log_dir, defaults.session_audio) == (None, False)


class _ScriptedSTT:
    language = "en"

    def transcribe_utterance(self, pcm, language=None):  # noqa: ARG002
        return Transcription("turn to John 3:16", 5.0)


class _Recording:
    def __init__(self):
        self.sent: list = []

    def submit(self, *args) -> None:
        self.sent.append(args)


def test_pipeline_logs_each_processed_utterance(tmp_path):
    proc = BibleLiveAudioProcessor.__new__(BibleLiveAudioProcessor)
    proc.config = AudioPipelineConfig()
    proc.stt = _ScriptedSTT()
    proc.metrics = PipelineMetrics()
    proc._frame_duration_s = 0.03
    proc._local = threading.local()
    proc.sender = _Recording()
    proc.transcript_sender = _Recording()
    proc._consecutive_transcription_errors = 0
    proc.bible_db_path = str(tmp_path / "missing.sqlite")
    proc.verse_text_db_path = None
    proc._parser_problems = []
    proc._init_thread_local_parsers()
    proc.session_log = SessionLog(tmp_path / "sessions", False, {}, 0.03)

    proc._process_utterance("en", [FRAME] * 40)
    proc.session_log.close()

    utterance = _records(proc.session_log)[1]
    assert utterance["transcripts"][0]["text"] == "turn to John 3:16"
    assert [r["reference"] for r in utterance["references"]] == ["John 3:16"]
    assert len(proc.sender.sent) == 1


class _CheckingQueue:
    """A vad_queue whose put() checks the utterance is already registered."""

    def __init__(self, log: SessionLog, full: bool = False):
        self.log, self.full, self.registered_at_put = log, full, None

    def put(self, item, timeout=None):  # noqa: ARG002
        self.registered_at_put = id(item[1]) in self.log._pending
        if self.full:
            from queue import Full

            raise Full

    def qsize(self) -> int:
        return 0


def _queueing_processor(tmp_path, full: bool):
    from audio.segmenter import SegmentEvent

    proc = BibleLiveAudioProcessor.__new__(BibleLiveAudioProcessor)
    proc.metrics = PipelineMetrics()
    proc._frame_duration_s = 0.03
    proc._capture_language = "lt"
    proc.session_log = SessionLog(tmp_path, False, {}, 0.03)
    proc.vad_queue = _CheckingQueue(proc.session_log, full)
    event = SegmentEvent("end", frames=[FRAME] * 20, reason="silence")
    return proc, event


def test_utterance_position_is_registered_before_it_is_queued(tmp_path):
    proc, event = _queueing_processor(tmp_path, full=False)
    proc._queue_speech_segment(event)
    assert proc.vad_queue.registered_at_put is True
    proc.session_log.close()


def test_utterance_dropped_by_a_full_queue_is_forgotten(tmp_path):
    proc, event = _queueing_processor(tmp_path, full=True)
    proc._queue_speech_segment(event)
    assert proc.session_log._pending == {}
    proc.session_log.close()
    assert _records(proc.session_log)[-1]["type"] == "utterance_dropped"
