"""Sending detected Bible references to the BibleLive server."""

import hashlib
import logging
import random
import threading
import time
from queue import Empty, Full, Queue
from typing import Any

import requests

from .config import AudioPipelineConfig
from .metrics import PipelineMetrics

logger = logging.getLogger(__name__)


class ReferenceSender:
    """Send references to the server with deduplication and retry-with-backoff.

    The live pipeline calls :meth:`submit`, which only queues the reference: a
    worker thread (:meth:`start`) does the HTTP, so a slow or unreachable server
    costs the transcription thread nothing. Before this, one reference could hold
    transcription for its whole retry schedule (about 23 s) while speech piled up.
    :meth:`send` delivers on the calling thread and is what the worker and the
    benchmark use.
    """

    def __init__(
        self,
        server_url: str,
        config: AudioPipelineConfig,
        metrics: PipelineMetrics,
    ):
        self.server_url = server_url
        self.config = config
        self.metrics = metrics
        # Deduplication: track recently sent references (ref_hash -> timestamp)
        self.recent_references: dict[str, float] = {}
        # Why the most recent reference never reached the server, or None once
        # one gets through. Reported to the operator in the pipeline heartbeat;
        # a reference the server rejects on its merits is not a delivery failure.
        self.delivery_problem: str | None = None
        # References waiting for the worker, each with when it was submitted
        # (time.monotonic()). Bounded: while the server is down, the newest
        # detections are the ones worth keeping.
        self._queue: Queue[tuple[float, Any]] = Queue(maxsize=config.send_queue_maxsize)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _retry_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter, capped at api_retry_max_delay."""
        # mypy types min(float, float) as Any (reproduced with a minimal case
        # against mypy 2.4), so wrap the result to honor the declared return type.
        return float(
            min(
                self.config.api_retry_base_delay * (2**attempt) + random.uniform(0, 0.1),
                self.config.api_retry_max_delay,
            )
        )

    def _get_reference_hash(self, ref) -> str:
        """Generate a hash for deduplication"""
        key = f"{ref.book}:{ref.chapter}:{ref.verse_start}-{ref.verse_end}"
        return hashlib.md5(key.encode()).hexdigest()[:16]

    def _is_duplicate(self, ref) -> bool:
        """Check if reference was recently sent (within dedup window)"""
        ref_hash = self._get_reference_hash(ref)
        current_time = time.time()

        # Clean up old entries
        expired_keys = [
            k
            for k, t in self.recent_references.items()
            if current_time - t > self.config.dedup_window_seconds
        ]
        for k in expired_keys:
            del self.recent_references[k]

        # Check if this reference was recently sent
        if ref_hash in self.recent_references:
            time_since = current_time - self.recent_references[ref_hash]
            if time_since < self.config.dedup_window_seconds:
                return True

        # Record this reference
        self.recent_references[ref_hash] = current_time
        return False

    def _skip_duplicate(self, ref) -> bool:
        """Count and log ``ref`` if it is a duplicate; return whether it was."""
        if not self._is_duplicate(ref):
            return False
        self.metrics.references_deduplicated += 1
        logger.info(
            f"Skipping duplicate (within {self.config.dedup_window_seconds}s window): {ref}"
        )
        return True

    def _sleep_before_retry(self, attempt: int) -> bool:
        """Count a retry and back off; return False if the sender is stopping."""
        self.metrics.api_retries += 1
        return not self._stop.wait(self._retry_delay(attempt))

    # ------------------------------------------------------------------
    # Background delivery (the live pipeline)
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the delivery worker."""
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="reference-sender")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Send what is queued, then stop the worker.

        Queued references still go out (the pipeline flushes held chapter-only
        references on shutdown, just before this), but each gets a single
        attempt: retry back-off is cut short once stopping. The wait is bounded
        by ``timeout``; whatever is left after it is logged. The worker is a
        daemon thread, so it cannot hold the process open.
        """
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        unsent = self._queue.qsize()
        if unsent:
            logger.warning(f"Stopping with {unsent} detected reference(s) not yet sent")

    def submit(self, ref) -> None:
        """Queue ``ref`` for the worker without blocking (transcription thread)."""
        if self._skip_duplicate(ref):
            return
        item = (time.monotonic(), ref)
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except Full:
                # The server is not keeping up; the reference waiting longest
                # is the least useful one to the operator now.
                try:
                    _, dropped = self._queue.get_nowait()
                except Empty:
                    continue  # the worker just made room
                self._drop(dropped, "the send queue is full")

    def _run(self) -> None:
        # Exits only once stopping *and* the queue is empty, so references
        # submitted just before stop() are delivered rather than abandoned.
        while True:
            try:
                submitted_at, ref = self._queue.get(timeout=0.2)
            except Empty:
                if self._stop.is_set():
                    return
                continue
            self._deliver(ref, submitted_at)

    def _drop(self, ref, reason: str) -> None:
        self.metrics.references_dropped += 1
        self.delivery_problem = f"it was discarded because {reason}"
        logger.warning(f"Discarding detected reference {ref}: {reason}")

    def _is_stale(self, ref, submitted_at: float | None) -> bool:
        """Drop ``ref`` if it waited longer than ``send_max_age_s``.

        Late is worse than missing here: with auto-approve on, a reference that
        reached a recovered server minutes after it was spoken would go straight
        onto the display, in the middle of something else.
        """
        if submitted_at is None:
            return False
        age = time.monotonic() - submitted_at
        if age <= self.config.send_max_age_s:
            return False
        self._drop(ref, f"it could not be sent within {self.config.send_max_age_s:.0f}s")
        return True

    # ------------------------------------------------------------------
    # Delivery on the calling thread
    # ------------------------------------------------------------------
    def send(self, ref) -> None:
        """Send a Bible reference to the BibleLive server with retry logic"""
        if self._skip_duplicate(ref):
            return
        self._deliver(ref)

    def _deliver(self, ref, submitted_at: float | None = None) -> None:
        """POST ``ref``, retrying server errors and timeouts with backoff.

        ``submitted_at`` is set for queued references: one that has become too
        old is dropped before each attempt instead of being sent late.
        """
        reference_data = {
            "book": ref.book,
            "chapter": ref.chapter,
            "verse_start": ref.verse_start,
            "verse_end": ref.verse_end,
            "confidence": ref.confidence,
            "inferred": ref.inferred,
            "text": "",
        }

        # Only send the header when a token is configured, so a server without
        # authentication sees exactly the request it always did.
        auth_kwargs: dict[str, Any] = (
            {"headers": {"Authorization": f"Bearer {self.config.api_token}"}}
            if self.config.api_token
            else {}
        )

        # Retry with exponential backoff
        for attempt in range(self.config.api_max_retries + 1):
            if self._is_stale(ref, submitted_at):
                return
            try:
                start_time = time.time()

                response = requests.post(
                    f"{self.server_url}/api/reference",
                    json=reference_data,
                    timeout=self.config.api_timeout,
                    **auth_kwargs,
                )

                duration_ms = (time.time() - start_time) * 1000
                self.metrics.record_api_duration(duration_ms)

                if response.status_code in (200, 201):
                    self.metrics.references_sent += 1
                    self.delivery_problem = None
                    logger.info(f"Sent to server ({duration_ms:.0f}ms): {ref}")
                    return
                elif response.status_code >= 500:
                    # Server error - retry
                    logger.warning(
                        f"Server error {response.status_code}, "
                        f"attempt {attempt + 1}/{self.config.api_max_retries + 1}"
                    )
                    if attempt < self.config.api_max_retries:
                        if not self._sleep_before_retry(attempt):
                            break
                        continue
                elif response.status_code == 401:
                    # Every later reference would fail the same way, so say how to fix it.
                    logger.error(
                        f"Server rejected reference {ref} as unauthorized: set "
                        "BIBLELIVE_OPERATOR_TOKEN (or --server-token) to the server's "
                        "operator token"
                    )
                    self.metrics.references_failed += 1
                    self.delivery_problem = "the server rejected the pipeline's token"
                    return
                else:
                    # Client error - don't retry
                    logger.warning(
                        f"Failed to send reference {ref}: {response.status_code} - {response.text}"
                    )
                    self.metrics.references_failed += 1
                    return

            except requests.exceptions.Timeout:
                logger.warning(
                    f"Timeout sending reference, attempt {attempt + 1}/{self.config.api_max_retries + 1}"
                )
                if attempt < self.config.api_max_retries:
                    if not self._sleep_before_retry(attempt):
                        break
                    continue
            except requests.exceptions.ConnectionError as e:
                logger.warning(
                    f"Connection error: {e}, attempt {attempt + 1}/{self.config.api_max_retries + 1}"
                )
                if attempt < self.config.api_max_retries:
                    if not self._sleep_before_retry(attempt):
                        break
                    continue
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error sending reference: {e}")
                self.metrics.references_failed += 1
                self.delivery_problem = "a request error"
                return
            except Exception as e:
                logger.error(f"Unexpected error sending reference: {e}", exc_info=True)
                self.metrics.references_failed += 1
                self.delivery_problem = "an unexpected error"
                return

        # All retries exhausted (or the sender is stopping)
        self.metrics.references_failed += 1
        self.delivery_problem = "the server kept failing or did not answer"
        logger.error(
            f"Failed to send reference after {self.config.api_max_retries + 1} attempts: {ref}"
        )
