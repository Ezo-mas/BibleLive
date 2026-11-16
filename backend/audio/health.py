"""Build heartbeat reports without mutating pipeline or capture state."""

import threading
import time
from typing import Any

from .capture_supervisor import SILENT_MIC_S, CaptureSupervisor

# Seconds without a single audio frame before the operator is told capture has
# stalled. Frames arrive every 30 ms, so this is far beyond any normal hiccup.
AUDIO_STALL_S = 5.0


def _health_issue(code: str, text: str, **params: str | int | float) -> dict[str, Any]:
    """One health-report problem: a stable ``code``, its ``params``, and the English ``text``."""
    return {"code": code, "params": params, "text": text}


def build_health_report(
    capture: CaptureSupervisor,
    *,
    language: str,
    interval_s: float,
    is_running: bool,
    vad_thread: threading.Thread | None,
    transcription_thread: threading.Thread | None,
    consecutive_transcription_errors: int,
    consecutive_utterance_failures: int,
    delivery_problem: str | None,
    parser_issues: list[dict[str, Any]],
) -> dict:
    """Read health inputs in the operator's established issue order."""
    issues: list[dict[str, Any]] = []
    # An uncaught exception ends a worker thread silently; this thread
    # would otherwise go on reporting a pipeline that detects nothing.
    for thread, role in (
        (vad_thread, "listening"),
        (transcription_thread, "transcription"),
    ):
        if is_running and thread is not None and not thread.is_alive():
            issues.append(
                _health_issue(
                    "thread_stopped",
                    f"The pipeline's {role} thread has stopped; restart the pipeline",
                    role=role,
                )
            )
    if capture.down:
        issues.append(
            _health_issue(
                "capture_stopped",
                "Audio capture stopped; the pipeline is trying to restart it",
            )
        )
    elif is_running and capture.last_frame_at is not None:
        # Capture streams continuously (silence included), so a gap means
        # ffmpeg is stuck, not that the room is quiet.
        now = time.monotonic()
        silent_s = now - capture.last_frame_at
        if silent_s > AUDIO_STALL_S:
            seconds = round(silent_s)
            issues.append(
                _health_issue(
                    "audio_stalled",
                    f"No audio has arrived from the microphone for {seconds} s",
                    seconds=seconds,
                )
            )
        elif capture.last_loud_at is not None and now - capture.last_loud_at > SILENT_MIC_S:
            # Frames arrive but none has any sound in it: a dead or muted input.
            seconds = round(now - capture.last_loud_at)
            issues.append(
                _health_issue(
                    "mic_silent",
                    f"The microphone is silent: no sound for {seconds} s. "
                    "Check that the right microphone is selected and not muted.",
                    seconds=seconds,
                )
            )
    errors = consecutive_transcription_errors
    if errors:
        issues.append(
            _health_issue(
                "stt_failed",
                f"Speech recognition failed on the last {errors} "
                f"utterance{'s' if errors != 1 else ''}",
                count=errors,
            )
        )
    utterance_failures = consecutive_utterance_failures
    if utterance_failures >= 3:
        issues.append(
            _health_issue(
                "utterance_failed",
                f"Processing failed on the last {utterance_failures} "
                f"{'utterance; it was' if utterance_failures == 1 else 'utterances; they were'} skipped",
                count=utterance_failures,
            )
        )
    if delivery_problem:
        issues.append(
            _health_issue(
                "delivery_failed",
                f"The last detected reference was not delivered: {delivery_problem}",
                detail=delivery_problem,
            )
        )
    issues.extend(parser_issues)
    return {
        "language": language,
        "interval_s": interval_s,
        "problems": [issue["text"] for issue in issues],
        "issues": issues,
    }
