"""Metrics and observability for the audio pipeline."""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PipelineMetrics:
    """Track audio pipeline metrics for observability."""

    # Counters
    audio_chunks_captured: int = 0
    vad_segments_detected: int = 0
    transcription_attempts: int = 0
    transcription_successes: int = 0
    transcription_failures: int = 0  # the speech model raised: detection is broken
    transcriptions_empty: int = 0  # nothing heard (silence, or under 0.5 s): normal
    references_detected: int = 0
    references_low_confidence: int = 0  # downgraded by Whisper segment quality
    references_sent: int = 0
    references_failed: int = 0
    references_deduplicated: int = 0
    references_dropped: int = 0  # queued but discarded: queue full, or too old to send
    transcripts_failed: int = 0  # follow-along transcripts the server did not take
    transcripts_dropped: int = 0  # follow-along transcripts discarded: queue full
    partials_transcribed: int = 0  # partial transcripts run (follow-along only)
    partials_dropped: int = 0  # partials that failed or were not confident
    partial_audio_seconds: float = 0.0  # audio the partial transcripts covered, in total
    partial_transcription_ms: float = 0.0  # time the partial transcripts took, in total
    api_retries: int = 0
    capture_failures: int = 0  # ffmpeg exits that capture did not ask for
    capture_restarts: int = 0  # successful restarts after such an exit

    # Latency tracking (moving averages)
    transcription_duration_ms: list[float] = field(default_factory=list)
    api_request_duration_ms: list[float] = field(default_factory=list)

    # Queue depth tracking
    audio_queue_depth: int = 0
    vad_queue_depth: int = 0

    def record_transcription_duration(self, duration_ms: float) -> None:
        """Record transcription duration (keep last 100)."""
        self.transcription_duration_ms.append(duration_ms)
        if len(self.transcription_duration_ms) > 100:
            self.transcription_duration_ms.pop(0)

    def record_api_duration(self, duration_ms: float) -> None:
        """Record API request duration (keep last 100)."""
        self.api_request_duration_ms.append(duration_ms)
        if len(self.api_request_duration_ms) > 100:
            self.api_request_duration_ms.pop(0)

    def get_avg_transcription_duration(self) -> float:
        """Get average transcription duration in ms."""
        return (
            sum(self.transcription_duration_ms) / len(self.transcription_duration_ms)
            if self.transcription_duration_ms
            else 0.0
        )

    def get_avg_api_duration(self) -> float:
        """Get average API request duration in ms."""
        return (
            sum(self.api_request_duration_ms) / len(self.api_request_duration_ms)
            if self.api_request_duration_ms
            else 0.0
        )

    def log_summary(self) -> None:
        """Log metrics summary."""
        logger.info("=== Pipeline Metrics ===")
        logger.info(f"  Audio chunks: {self.audio_chunks_captured}")
        logger.info(
            f"  Capture failures: {self.capture_failures} ({self.capture_restarts} restarted)"
        )
        logger.info(f"  VAD segments: {self.vad_segments_detected}")
        logger.info(
            f"  Transcriptions: {self.transcription_successes}/{self.transcription_attempts} "
            f"({self.transcription_failures} failed, {self.transcriptions_empty} empty)"
        )
        logger.info(f"  Avg transcription time: {self.get_avg_transcription_duration():.1f}ms")
        logger.info(
            f"  References detected: {self.references_detected} "
            f"({self.references_low_confidence} low-confidence transcription)"
        )
        logger.info(
            f"  References sent: {self.references_sent} "
            f"({self.references_failed} failed, {self.references_dropped} dropped unsent, "
            f"{self.references_deduplicated} deduplicated)"
        )
        logger.info(
            f"  Follow-along transcripts: {self.transcripts_failed} failed, "
            f"{self.transcripts_dropped} dropped"
        )
        logger.info(
            f"  Partial transcripts: {self.partials_transcribed} "
            f"({self.partial_audio_seconds:.1f}s audio, "
            f"{self.partial_transcription_ms / 1000:.1f}s to transcribe, "
            f"{self.partials_dropped} dropped)"
        )
        logger.info(f"  API retries: {self.api_retries}")
        logger.info(f"  Avg API request time: {self.get_avg_api_duration():.1f}ms")
        logger.info(
            f"  Queue depths - Audio: {self.audio_queue_depth}, VAD: {self.vad_queue_depth}"
        )
