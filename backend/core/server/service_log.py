"""Optional coordinate-only service events, written outside the request thread."""

import contextlib
import json
import logging
import queue
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)

# Explicit allowlists prevent accidentally passing transcript or verse text.
EVENT_FIELDS = {
    "shown": {
        "translation",
        "book",
        "chapter",
        "verse_start",
        "verse_end",
        "source",
        "confidence",
        "inferred",
        "auto_approved",
    },
    "refused_no_text": {
        "translation",
        "book",
        "chapter",
        "verse_start",
        "verse_end",
        "source",
        "auto_approved",
    },
    "cleared": set(),
    "queue_cleared": {"count"},
    "pending_discarded": {"reference_id", "book", "chapter", "verse_start", "verse_end"},
    "queue_overflowed": {"count"},
    "translation_switched": {"from", "to"},
    "pipeline_status": {"status", "issue_codes"},
}


class JsonLinesHandler(logging.FileHandler):
    """The listener owns emits; startup/shutdown own opening and closing.

    FileHandler's lock protects the stream across those lifecycle boundaries.
    """

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            super().emit(record)

    def handleError(self, record: logging.LogRecord) -> None:
        pass


class ServiceLog:
    """Startup/event-loop-owned logger, handler reference and listener lifecycle.

    The event loop enqueues detached JSON strings using an unbounded SimpleQueue
    (put_nowait never waits for disk). The listener thread alone writes the file.
    On shutdown a daemon worker owns listener stop and handler close, allowing the
    event loop to time out a stalled disk. When disabled no listener is started.
    """

    def __init__(self) -> None:
        self.logger: logging.Logger | None = None
        self.listener: QueueListener | None = None
        self.handler: JsonLinesHandler | None = None
        directory = config.resolve_service_log_dir()
        if not directory:
            return
        try:
            folder = directory
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
            self.handler = JsonLinesHandler(folder / f"service-{stamp}.jsonl", encoding="utf-8")
            self.handler.setFormatter(logging.Formatter("%(message)s"))
            events: queue.SimpleQueue = queue.SimpleQueue()
            self.logger = logging.Logger(f"biblelive.service.{stamp}", level=logging.INFO)
            self.logger.propagate = False
            self.logger.addHandler(QueueHandler(events))
            self.listener = QueueListener(events, self.handler)
            self.listener.start()
        except Exception as exc:
            logger.warning("Could not start service log in %s: %s", directory, exc)
            if self.handler is not None:
                with contextlib.suppress(Exception):
                    self.handler.close()
            self.logger = None
            self.listener = None

    def emit(self, event: str, **fields: Any) -> None:
        if self.logger is None:
            return
        try:
            allowed = EVENT_FIELDS[event]
            payload = {"ts": datetime.now(UTC).isoformat(), "event": event}
            payload.update({key: value for key, value in fields.items() if key in allowed})
            self.logger.info(json.dumps(payload))
        except Exception:
            pass

    def stop(self) -> None:
        """Called only at shutdown, after requests finish; drain queued events."""
        try:
            if self.listener is not None:
                self.listener.stop()
            if self.handler is not None:
                self.handler.close()
        except Exception:
            pass
        self.logger = None
        self.listener = None
