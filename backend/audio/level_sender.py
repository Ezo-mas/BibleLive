"""Batch live microphone peaks for delivery off the capture thread."""

import logging
import threading
import time
from collections import deque

import requests

from .config import AudioPipelineConfig
from .levels import BLOCK_BYTES, block_level

logger = logging.getLogger(__name__)
SEND_INTERVAL = 0.2
MAX_TIMEOUT = 1.0
MAX_BATCH = 20  # Matches core.server.models.PipelineLevels.levels max_length.
WARNING_INTERVAL = 60.0
AUTH_RETRY_DELAY = 30.0


class _AuthRejected(RuntimeError):
    pass


class _LevelBuffer:
    def __init__(self) -> None:
        # One producer and one consumer, using atomic append and popleft.
        self.levels: deque[float] = deque(maxlen=MAX_BATCH)
        # Capture owns the bytes. The sender replaces the buffer with None
        # during an auth pause, then starts with an empty buffer on resume.
        self.partial: bytearray | None = bytearray()


class LevelSender:
    def __init__(self, server_url: str, config: AudioPipelineConfig):
        self.url = f"{server_url}/api/pipeline/levels"
        self.config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._buffer = _LevelBuffer()

    def note_frame(self, frame: bytes) -> None:
        buffer = self._buffer
        levels = buffer.levels
        partial = buffer.partial
        if partial is None:
            return
        partial.extend(frame)
        while len(partial) >= BLOCK_BYTES:
            level = block_level(partial[:BLOCK_BYTES])
            if buffer.partial is not partial:
                return
            levels.append(level)
            del partial[:BLOCK_BYTES]

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        # A timed-out worker keeps its own stop event and buffer until it exits.
        self._stop = threading.Event()
        self._buffer = _LevelBuffer()
        thread = threading.Thread(
            target=self._run, args=(self._stop, self._buffer), daemon=True, name="pipeline-levels"
        )
        try:
            thread.start()
        except RuntimeError as e:
            logger.warning("Could not start microphone level sender: %s", e)
        else:
            self._thread = thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def reset_capture(self) -> None:
        partial = self._buffer.partial
        if partial is not None:
            partial.clear()

    def _run(self, stop: threading.Event, buffer: _LevelBuffer) -> None:
        delay = SEND_INTERVAL
        next_warning = 0.0
        failure_level: int | None = None
        auth_reported = False
        with requests.Session() as session:
            while not stop.wait(delay):
                started = time.monotonic()
                try:
                    sent = self._send_batch(session, buffer.levels)
                except Exception as e:
                    auth_rejected = isinstance(e, _AuthRejected)
                    if failure_level is None or (auth_rejected and not auth_reported):
                        now = time.monotonic()
                        log_level = logging.DEBUG
                        if now >= next_warning:
                            log_level = logging.WARNING
                            next_warning = now + WARNING_INTERVAL
                        logger.log(log_level, "Could not send microphone levels: %s", e)
                        failure_level = max(failure_level or log_level, log_level)
                    if auth_rejected:
                        auth_reported = True
                        buffer.partial = None
                        buffer.levels.clear()
                        if stop.wait(AUTH_RETRY_DELAY):
                            return
                        # An in-flight capture write still points to the old deque.
                        buffer.levels = deque(maxlen=MAX_BATCH)
                        buffer.partial = bytearray()
                        delay = SEND_INTERVAL
                        continue
                else:
                    if sent and failure_level is not None:
                        recovery_level = (
                            logging.INFO if failure_level == logging.WARNING else logging.DEBUG
                        )
                        logger.log(
                            recovery_level, "Microphone levels are reaching the server again"
                        )
                        failure_level = None
                        auth_reported = False
                delay = max(0.0, SEND_INTERVAL - (time.monotonic() - started))

    def _send_batch(self, session: requests.Session, levels: deque[float]) -> bool:
        batch = [levels.popleft() for _ in range(len(levels))]
        if not batch:
            return False

        headers = (
            {"Authorization": f"Bearer {self.config.api_token}"} if self.config.api_token else {}
        )
        response = session.post(
            self.url,
            json={"levels": batch},
            headers=headers,
            timeout=min(self.config.api_timeout, MAX_TIMEOUT),
        )
        if response.status_code in (401, 403):
            raise _AuthRejected(f"HTTP {response.status_code}; pausing for {AUTH_RETRY_DELAY:g} s")
        if response.status_code >= 300:
            raise RuntimeError(f"HTTP {response.status_code}")
        return True
