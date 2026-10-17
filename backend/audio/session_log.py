"""Live session log: what the pipeline heard and detected during a service.

With ``--session-log-dir DIR`` every run writes ``DIR/<start time>.jsonl``: one
header line, then one line per utterance (where it sits in the service, each
speech model's transcript, the references sent and their confidence) and a
line per notable event (language switch, capture restart). With
``--session-audio on`` it also writes ``DIR/<start time>.wav``: everything the
microphone delivered, silence included, as 16 kHz mono PCM. Utterance times
(``start_s`` / ``end_s``) are positions in that WAV, so a logged service can be
annotated and added to the detection benchmark as it is.

The WAV header is rewritten on every write, so the file stays playable if the
PC loses power mid-service. Logging must never stop detection: a write error
is logged once and logging turns itself off.
"""

import json
import logging
import threading
import time
import wave
from pathlib import Path
from typing import Any

from core.verse_parser import Reference

from .stt import Transcription

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16-bit PCM
# Utterance timing entries kept for queued utterances; more than this many
# waiting means they were dropped, and the oldest are forgotten.
_MAX_PENDING = 500


def _reference_json(ref: Reference) -> dict[str, Any]:
    return {
        "reference": str(ref),
        "book": ref.book,
        "chapter": ref.chapter,
        "verse_start": ref.verse_start,
        "verse_end": ref.verse_end,
        "confidence": ref.confidence,
        "inferred": ref.inferred,
        "from_text": ref.from_text,
    }


def _transcription_json(result: Transcription) -> list[dict[str, Any]]:
    out = []
    for r in (result, result.alternate):
        if r is None:
            continue
        out.append(
            {
                "model": r.source,
                "text": r.text,
                "low_confidence_reason": r.low_confidence_reason,
                "failed": r.failed,
                "ms": round(r.duration_ms),
            }
        )
    return out


class SessionLog:
    """One service's transcript log (and optional audio), safe across pipeline threads.

    The VAD thread calls :meth:`write_frame`, :meth:`speech_started` and
    :meth:`utterance_queued`; the transcription thread calls :meth:`utterance`;
    any thread may call :meth:`event`.
    """

    def __init__(
        self,
        directory: str | Path,
        record_audio: bool,
        header: dict[str, Any],
        frame_duration_s: float,
    ):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stem = time.strftime("%Y-%m-%d_%H%M%S")
        self.transcript_path = directory / f"{stem}.jsonl"
        self.audio_path: Path | None = directory / f"{stem}.wav" if record_audio else None
        self._frame_s = frame_duration_s
        self._frames = 0  # frames delivered so far (the WAV's length, when recorded)
        self._speech_start_s = 0.0
        self._pending: dict[int, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._enabled = True

        self._file = self.transcript_path.open("a", encoding="utf-8")
        self._wav: wave.Wave_write | None = None
        if self.audio_path is not None:
            # Open for the whole session; close() ends it.
            self._wav = wave.open(str(self.audio_path), "wb")  # noqa: SIM115
            self._wav.setnchannels(1)
            self._wav.setsampwidth(SAMPLE_WIDTH)
            self._wav.setframerate(SAMPLE_RATE)
        self._write(
            {
                "type": "session",
                "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "audio_file": self.audio_path.name if self.audio_path else None,
                **header,
            }
        )
        logger.info(
            "Session log: %s%s",
            self.transcript_path,
            f" (audio: {self.audio_path.name})" if self.audio_path else "",
        )

    @property
    def position_s(self) -> float:
        """Seconds of audio delivered so far (the WAV position of the next frame)."""
        return self._frames * self._frame_s

    def _fail(self, error: Exception) -> None:
        if self._enabled:
            self._enabled = False
            logger.error(f"Session log stopped after a write error: {error}")

    def _write(self, record: dict[str, Any]) -> None:
        with self._lock:
            if not self._enabled:
                return
            try:
                self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
                self._file.flush()
            except Exception as e:
                self._fail(e)

    def write_frame(self, frame: bytes) -> None:
        """Record one captured frame (VAD thread)."""
        self._frames += 1
        if self._wav is None or not self._enabled:
            return
        try:
            # writeframes (unlike writeframesraw) keeps the header's length
            # current, so a crash leaves a playable file.
            self._wav.writeframes(frame)
        except Exception as e:
            self._fail(e)

    def speech_started(self, onset_frames: int) -> None:
        """An utterance began ``onset_frames`` frames before the latest one (VAD thread)."""
        self._speech_start_s = max(0.0, self.position_s - onset_frames * self._frame_s)

    def utterance_queued(self, frames: list[bytes], trailing_silent_frames: int) -> None:
        """Remember where a queued utterance sits in the service (VAD thread)."""
        end_s = self.position_s - trailing_silent_frames * self._frame_s
        with self._lock:
            if len(self._pending) >= _MAX_PENDING:
                self._pending.pop(next(iter(self._pending)))
            self._pending[id(frames)] = (min(self._speech_start_s, end_s), end_s)

    def utterance(
        self,
        frames: list[bytes],
        language: str,
        result: Transcription | None,
        references: list[Reference],
    ) -> None:
        """Log one processed utterance and the references sent for it (transcription thread)."""
        with self._lock:
            span = self._pending.pop(id(frames), None)
        self._write(
            {
                "type": "utterance",
                "time": time.strftime("%H:%M:%S"),
                "start_s": round(span[0], 2) if span else None,
                "end_s": round(span[1], 2) if span else None,
                "audio_s": round(len(frames) * self._frame_s, 2),
                "language": language,
                "transcripts": _transcription_json(result) if result is not None else [],
                "references": [_reference_json(r) for r in references],
            }
        )

    def event(self, kind: str, **fields: Any) -> None:
        """Log something that happened between utterances (any thread)."""
        self._write(
            {
                "type": kind,
                "time": time.strftime("%H:%M:%S"),
                "at_s": round(self.position_s, 2),
                **fields,
            }
        )

    def references(self, kind: str, references: list[Reference]) -> None:
        """Log references sent outside an utterance (e.g. held ones flushed at stop)."""
        if references:
            self.event(kind, references=[_reference_json(r) for r in references])

    def close(self) -> None:
        with self._lock:
            self._enabled = False
            for closer in (self._file.close, self._wav.close if self._wav else None):
                if closer is None:
                    continue
                try:
                    closer()
                except Exception as e:
                    logger.warning(f"Error closing session log: {e}")
