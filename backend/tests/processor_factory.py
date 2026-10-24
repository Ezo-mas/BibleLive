"""Builds a BibleLiveAudioProcessor for tests without running its constructor.

The constructor loads a Whisper model, opens the VAD and builds the senders, which
tests do not need. ``make_processor`` sets every attribute ``__init__`` sets, to the
value ``__init__`` gives for a default config, except the models, the audio capture,
and the senders and heartbeat that run threads. Those are ``None`` unless a test
passes a fake.
"""

import threading
import time
from queue import Queue
from typing import Any

from audio.config import AudioPipelineConfig
from audio.metrics import PipelineMetrics
from audio.partials import PartialMailbox, PartialScheduler, PartialTranscript
from audio.pipeline import BibleLiveAudioProcessor
from audio.segmenter import FRAME_DURATION_MS, Segmenter
from audio.stt_gguf import engine_models


def make_processor(**overrides: Any) -> BibleLiveAudioProcessor:
    """A processor as ``__init__`` leaves it, with ``overrides`` (attribute names) applied.

    An unknown attribute name raises TypeError, so a misspelt one cannot pass silently.
    """
    config = overrides.get("config") or AudioPipelineConfig()
    partials_on = config.partial_transcripts
    defaults: dict[str, Any] = {
        "config": config,
        "bible_db_path": "bible_data/biblelive.sqlite",
        "verse_text_db_path": None,
        "server_url": "http://localhost:8000",
        # Not built: models, audio capture, and the senders and heartbeat that run threads.
        "audio_capture": None,
        "vad": None,
        "stt": None,
        "sender": None,
        "transcript_sender": None,
        "heartbeat": None,
        "control_server": None,
        "vad_thread": None,
        "transcription_thread": None,
        "_stt_models": engine_models(config.stt_engine),
        "vad_queue": Queue(maxsize=config.vad_queue_maxsize),
        "is_running": False,
        "frame_duration_ms": FRAME_DURATION_MS,
        "_frame_duration_s": FRAME_DURATION_MS / 1000,
        # Without a VAD, the segmenter hears silence.
        "segmenter": Segmenter.from_config(lambda frame: False, config),
        "metrics": PipelineMetrics(),
        "last_metrics_log": time.time(),
        "_partial_schedule": PartialScheduler.from_config(config) if partials_on else None,
        "_partial_mailbox": PartialMailbox(),
        "_partial_transcript": PartialTranscript(config.partial_mode) if partials_on else None,
        "_utterance_seq": 0,
        "_local": threading.local(),
        "_capture_language": config.language,
        "_pending_language": None,
        "_pending_language_lock": threading.Lock(),
        "_capture_lock": threading.Lock(),
        "_capture_restart_attempt": 0,
        "_capture_restarted_at_s": None,
        "_capture_down": False,
        "_last_frame_at": None,
        "_last_loud_at": None,
        "_mic_silent_logged": False,
        "_consecutive_transcription_errors": 0,
        "_parser_issues": [],
        "session_log": None,
    }
    unknown = overrides.keys() - defaults.keys()
    if unknown:
        raise TypeError(f"make_processor() got unknown attributes: {', '.join(sorted(unknown))}")
    proc = BibleLiveAudioProcessor.__new__(BibleLiveAudioProcessor)
    for name, value in {**defaults, **overrides}.items():
        setattr(proc, name, value)
    return proc
