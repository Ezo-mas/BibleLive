"""Sending detected Bible references to the BibleLive server."""

import hashlib
import logging
import random
import time

import requests

from .config import AudioPipelineConfig
from .metrics import PipelineMetrics

logger = logging.getLogger(__name__)


class ReferenceSender:
    """Send references to the server with deduplication and retry-with-backoff."""

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

    def _retry_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter, capped at api_retry_max_delay."""
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

    def _sleep_before_retry(self, attempt: int) -> None:
        """Count a retry and back off before the next attempt."""
        self.metrics.api_retries += 1
        time.sleep(self._retry_delay(attempt))

    def send(self, ref) -> None:
        """Send a Bible reference to the BibleLive server with retry logic"""
        # Check for duplicate
        if self._is_duplicate(ref):
            self.metrics.references_deduplicated += 1
            logger.info(
                f"Skipping duplicate (within {self.config.dedup_window_seconds}s window): {ref}"
            )
            return

        reference_data = {
            "book": ref.book,
            "chapter": ref.chapter,
            "verse_start": ref.verse_start,
            "verse_end": ref.verse_end,
            "confidence": ref.confidence,
            "inferred": ref.inferred,
            "text": "",
        }

        # Retry with exponential backoff
        for attempt in range(self.config.api_max_retries + 1):
            try:
                start_time = time.time()

                response = requests.post(
                    f"{self.server_url}/api/reference",
                    json=reference_data,
                    timeout=self.config.api_timeout,
                )

                duration_ms = (time.time() - start_time) * 1000
                self.metrics.record_api_duration(duration_ms)

                if response.status_code in (200, 201):
                    self.metrics.references_sent += 1
                    logger.info(f"Sent to server ({duration_ms:.0f}ms): {ref}")
                    return
                elif response.status_code >= 500:
                    # Server error - retry
                    logger.warning(
                        f"Server error {response.status_code}, "
                        f"attempt {attempt + 1}/{self.config.api_max_retries + 1}"
                    )
                    if attempt < self.config.api_max_retries:
                        self._sleep_before_retry(attempt)
                        continue
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
                    self._sleep_before_retry(attempt)
                    continue
            except requests.exceptions.ConnectionError as e:
                logger.warning(
                    f"Connection error: {e}, attempt {attempt + 1}/{self.config.api_max_retries + 1}"
                )
                if attempt < self.config.api_max_retries:
                    self._sleep_before_retry(attempt)
                    continue
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error sending reference: {e}")
                self.metrics.references_failed += 1
                return
            except Exception as e:
                logger.error(f"Unexpected error sending reference: {e}", exc_info=True)
                self.metrics.references_failed += 1
                return

        # All retries exhausted
        self.metrics.references_failed += 1
        logger.error(
            f"Failed to send reference after {self.config.api_max_retries + 1} attempts: {ref}"
        )
