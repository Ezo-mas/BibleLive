"""Sending confident transcripts to the server for follow-along hints.

The server uses a transcript to tell operators which verse the speaker reads next,
after the passage on screen (see core/server/follow_along.py). That is a
convenience, so delivery is best effort: one attempt per transcript and no retry.
A failure only costs that hint. It is never reported as a delivery problem, which
is the reference sender's job. :meth:`submit` only queues, so transcription never
waits on the network.
"""

import logging
import threading
from queue import Empty, Full, Queue
from typing import Any

import requests

from .config import AudioPipelineConfig
from .metrics import PipelineMetrics

logger = logging.getLogger(__name__)


class TranscriptSender:
    """Post transcripts to ``/api/transcript`` from a background thread."""

    def __init__(
        self,
        server_url: str,
        config: AudioPipelineConfig,
        metrics: PipelineMetrics,
    ):
        self.url = f"{server_url}/api/transcript"
        self.config = config
        self.metrics = metrics
        # (text, language) waiting for the worker. Bounded: the newest are kept.
        self._queue: Queue[tuple[str, str]] = Queue(maxsize=config.transcript_queue_maxsize)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Set once an older server answers 404 (it has no such endpoint). Posting
        # more would only fail, so transcripts stop being sent.
        self._unsupported = False
        # Set while the server's latest answer says follow-along is armed. The VAD
        # thread reads it; the worker and stop() write it (see :attr:`armed`).
        self._armed = threading.Event()

    @property
    def armed(self) -> bool:
        """Whether the server wants partial transcripts of the utterance in progress.

        True only after a successful post whose reply has ``follow_along_active``
        true. A failed post, a non-2xx answer, an older server (404), a reply
        without the field, and :meth:`stop` all make it False.
        """
        return self._armed.is_set()

    def start(self) -> None:
        """Start the posting worker."""
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="transcript-sender")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the worker. Transcripts still queued are discarded, not sent."""
        self._stop.set()
        self._armed.clear()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break

    def submit(self, text: str, language: str) -> None:
        """Queue a transcript for the worker without blocking (transcription thread)."""
        if self._unsupported:
            return
        item = (text, language)
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except Full:
                # The newest transcript is the one most likely to be read now.
                try:
                    self._queue.get_nowait()
                except Empty:
                    continue  # the worker just made room
                self.metrics.transcripts_dropped += 1

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                text, language = self._queue.get(timeout=0.2)
            except Empty:
                continue
            if not self._unsupported:  # items queued before a 404 are dropped
                self._post(text, language)

    def _post(self, text: str, language: str) -> None:
        """POST one transcript, once. A failure is logged at DEBUG and counted."""
        # Same header rule as the reference sender: none unless a token is configured.
        auth_kwargs: dict[str, Any] = (
            {"headers": {"Authorization": f"Bearer {self.config.api_token}"}}
            if self.config.api_token
            else {}
        )
        try:
            response = requests.post(
                self.url,
                json={"text": text, "language": language},
                timeout=self.config.transcript_timeout,
                **auth_kwargs,
            )
        except Exception as e:
            self._set_armed(False)
            self._failed(f"the request failed: {e}")
            return

        if response.status_code == 404:
            self._set_armed(False)
            if self._unsupported:
                return
            self._unsupported = True
            logger.info(
                "The server has no follow-along endpoint (it is an older version); "
                "transcripts will not be sent to it"
            )
        elif response.status_code >= 300:
            self._set_armed(False)
            self._failed(f"the server answered {response.status_code}")
        else:
            self._set_armed(_says_armed(response))

    def _set_armed(self, armed: bool) -> None:
        if armed:
            self._armed.set()
        else:
            self._armed.clear()

    def _failed(self, reason: str) -> None:
        self.metrics.transcripts_failed += 1
        logger.debug(f"Follow-along transcript not delivered: {reason}")


def _says_armed(response: requests.Response) -> bool:
    """Whether a 2xx reply says follow-along is armed; a reply that is not that is not armed."""
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("follow_along_active") is True
