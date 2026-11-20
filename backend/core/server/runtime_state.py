"""Coordinate-only recovery state; never a source of verse text."""

import asyncio
import contextlib
import logging
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .models import BibleReference
from .settings_store import SettingsStore

logger = logging.getLogger(__name__)
RESTORE_DISPLAY_MAX_AGE_S = 600


class RuntimeReference(BaseModel):
    """Recovery metadata, owned by the server event-loop thread."""

    translation: str | None
    book: str
    chapter: int
    verse_start: int | None = None
    verse_end: int | None = None
    source: Literal["detected", "manual", "follow"]
    confidence: Literal["high", "medium", "low"] | None = None
    inferred: bool = False
    shown_at: float = Field(allow_inf_nan=False)

    @classmethod
    def from_reference(cls, ref: BibleReference, translation: str | None) -> "RuntimeReference":
        return cls(
            translation=translation,
            shown_at=time.time(),
            **ref.model_dump(
                include={
                    "book",
                    "chapter",
                    "verse_start",
                    "verse_end",
                    "source",
                    "confidence",
                    "inferred",
                }
            ),
        )


class RuntimeSnapshot(BaseModel):
    """Event-loop-owned recovery snapshot; worker threads receive detached dicts."""

    current: RuntimeReference | None = None
    history: list[RuntimeReference] = Field(default_factory=list, max_length=20)


class RuntimeStateStore:
    """Coalesce saves without awaiting disk on the display path.

    The event-loop thread owns ``snapshot``, ``_pending`` and ``_task``. The
    daemon writer only sees detached JSON dictionaries and the immutable writer
    path; it never mutates server state. Only one write runs at a time.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.snapshot = RuntimeSnapshot()
        self._writer = SettingsStore(path)
        self._pending: dict | None = None
        self._task: asyncio.Task[None] | None = None

    def load(self) -> RuntimeSnapshot:
        """Ignore missing or damaged recovery files, with a diagnostic."""
        try:
            self.snapshot = RuntimeSnapshot.model_validate_json(self.path.read_bytes())
        except FileNotFoundError:
            self.snapshot = RuntimeSnapshot()
        except (OSError, ValueError, ValidationError) as exc:
            logger.warning("Ignoring runtime state %s: %s", self.path, exc)
            self.snapshot = RuntimeSnapshot()
        return self.snapshot

    def save_snapshot(self) -> bool:
        """Persist the current snapshot synchronously during startup."""
        try:
            return self._writer.write_json(self.snapshot.model_dump(mode="json"))
        except Exception:
            logger.warning("Could not persist runtime state %s", self.path, exc_info=True)
            return False

    def record(self, ref: BibleReference, translation: str | None) -> None:
        record = RuntimeReference.from_reference(ref, translation)
        self.snapshot = RuntimeSnapshot(
            current=record, history=[record, *self.snapshot.history][:20]
        )
        self._schedule()

    def clear(self) -> None:
        self.snapshot = RuntimeSnapshot(history=self.snapshot.history)
        self._schedule()

    def clear_history(self) -> None:
        self.snapshot = RuntimeSnapshot(current=self.snapshot.current)
        self._schedule()

    def _schedule(self) -> None:
        self._pending = self.snapshot.model_dump(mode="json")
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._write_pending())

    async def _write_pending(self) -> None:
        # A burst within this window writes only its latest snapshot.
        await asyncio.sleep(0.05)
        while self._pending is not None:
            pending, self._pending = self._pending, None
            await self._write_one(pending)

    async def _write_one(self, pending: dict) -> None:
        loop = asyncio.get_running_loop()
        done = asyncio.Event()

        def write() -> None:
            try:
                self._writer.write_json(pending)
            except Exception:
                logger.warning("Could not persist runtime state %s", self.path, exc_info=True)
            finally:
                # The daemon can finish after shutdown has closed the loop.
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(done.set)

        try:
            threading.Thread(target=write, daemon=True, name="runtime-state-write").start()
            await done.wait()
        except Exception:
            logger.warning("Could not start runtime state write %s", self.path, exc_info=True)

    async def flush(self) -> None:
        """Finish the latest queued write during orderly shutdown."""
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=2)
            except TimeoutError:
                logger.warning("Timed out flushing runtime state %s", self.path)
            except Exception:
                logger.warning("Could not flush runtime state %s", self.path, exc_info=True)
