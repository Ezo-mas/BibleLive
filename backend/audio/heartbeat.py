"""Periodic pipeline health report to the BibleLive server.

Without it the server cannot tell a quiet sermon from a pipeline that has
stopped detecting: a dead microphone, a Whisper error on every utterance, or a
pipeline that is not running at all all look like "no references". The pipeline
posts a small status on its own thread every ``heartbeat_interval`` seconds; the
server keeps the latest one and reports the pipeline as offline once they stop,
which is what lets the operator console warn about it.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

import requests

from .config import AudioPipelineConfig

logger = logging.getLogger(__name__)


class HeartbeatSender:
    """Post ``get_status()`` to ``/api/pipeline/heartbeat`` on a background thread."""

    def __init__(
        self,
        server_url: str,
        config: AudioPipelineConfig,
        get_status: Callable[[], dict[str, Any]],
    ):
        self.url = f"{server_url}/api/pipeline/heartbeat"
        self.config = config
        self.get_status = get_status
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Log a failure once per run of failures, not every few seconds.
        self._failing = False

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="pipeline-heartbeat")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        # Report straight away so the console sees a starting pipeline promptly.
        while True:
            self.beat()
            if self._stop.wait(self.config.heartbeat_interval):
                return

    def beat(self) -> bool:
        """Send one heartbeat; return whether the server accepted it."""
        headers = (
            {"Authorization": f"Bearer {self.config.api_token}"} if self.config.api_token else {}
        )
        try:
            response = requests.post(
                self.url,
                json=self.get_status(),
                headers=headers,
                # A heartbeat that cannot arrive before the next one is worthless.
                timeout=min(self.config.api_timeout, self.config.heartbeat_interval),
            )
        except requests.exceptions.RequestException as e:
            self._report_failure(f"Could not reach the server for a health report: {e}")
            return False
        except Exception as e:
            self._report_failure(f"Unexpected error sending a health report: {e}")
            return False

        if response.status_code == 401:
            self._report_failure(
                "Server rejected the pipeline's health report as unauthorized: set "
                "BIBLELIVE_OPERATOR_TOKEN (or --server-token) to the server's operator token",
                level=logging.ERROR,
            )
            return False
        if response.status_code >= 300:
            self._report_failure(f"Server rejected a health report: {response.status_code}")
            return False

        if self._failing:
            logger.info("Health reports are reaching the server again")
            self._failing = False
        return True

    def _report_failure(self, message: str, level: int = logging.WARNING) -> None:
        if not self._failing:
            logger.log(level, message)
            self._failing = True
