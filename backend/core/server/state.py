"""Application state management."""

import logging
import time
from typing import Literal

from fastapi import WebSocket

from ..bible_lookup import BibleLookup
from ..config import config
from ..passage import ResolvedPassage
from .connections import ConnectionManager
from .models import (
    AutoMode,
    BibleReference,
    DisplaySettings,
    PipelineHeartbeat,
    PipelineStatusResponse,
    VerseText,
)
from .settings_store import SettingsStore, coerce_auto_mode, is_auto_mode

logger = logging.getLogger(__name__)

# Verse limits an operator may choose. Kept in step with the operator console's
# options; anything else is rejected so a typo cannot widen or empty the display.
ALLOWED_VERSE_LIMITS: tuple[int | str, ...] = (3, 5, 10, "all")

# Background themes a display may use. Kept in step with the operator console's
# options; anything else is rejected so a bad value cannot blank the projector.
ALLOWED_DISPLAY_THEMES: tuple[str, ...] = ("dark", "light")

# Confidence levels each auto-approve mode accepts. Confidence order is
# low < medium < high, so "medium_and_up" ("Med+" in the console) covers high as
# well. "off" keeps every detection waiting for the operator.
AUTO_APPROVE_CONFIDENCES: dict[AutoMode, frozenset[str]] = {
    "off": frozenset(),
    "high": frozenset({"high"}),
    "medium_and_up": frozenset({"high", "medium"}),
    "all": frozenset({"high", "medium", "low"}),
}

# How many report intervals the audio pipeline may miss before the operator is
# told it is offline. One late report (a slow request, a GC pause) is not news.
PIPELINE_MISSED_REPORTS_OFFLINE = 3


class AppState:
    """Manage the application state including pending references, current display, and connections."""

    def __init__(
        self, bible_lookup: BibleLookup, settings_store: SettingsStore | None = None
    ) -> None:
        """Initialize the application state with empty references and connection manager.

        Args:
            bible_lookup: Loaded database used to resolve verse text.
            settings_store: Where persisted operator settings live. Defaults to
                ``config.SETTINGS_PATH``; tests pass a temporary file.
        """
        self.current_display: BibleReference | None = None
        self.pending_references: list[BibleReference] = []
        self.reference_history: list[BibleReference] = []
        self.display_settings = DisplaySettings()
        self.connection_manager = ConnectionManager()
        self.bible_lookup = bible_lookup
        self.settings_store = (
            settings_store
            if settings_store is not None
            else SettingsStore(config.resolve_settings_path())
        )
        # Precedence: a persisted mode wins over the environment default, and an
        # unknown environment value leaves auto-approval off.
        self.auto_mode: AutoMode = self.settings_store.load_auto_mode(
            default=coerce_auto_mode(config.AUTO_MODE)
        )
        # The audio pipeline's latest health report and when it arrived
        # (time.monotonic()), or None until the first one.
        self.pipeline_report: PipelineHeartbeat | None = None
        self.pipeline_reported_at: float | None = None

    def record_pipeline_report(self, report: PipelineHeartbeat, now: float | None = None) -> None:
        """Keep the audio pipeline's latest health report."""
        self.pipeline_report = report
        self.pipeline_reported_at = time.monotonic() if now is None else now

    def pipeline_status(self, now: float | None = None) -> PipelineStatusResponse:
        """Summarize the pipeline's health for the operator console.

        The pipeline cannot report that it has died, so silence is the signal:
        once more than ``PIPELINE_MISSED_REPORTS_OFFLINE`` of its own report
        intervals pass without a report, it is offline, whatever it said last.
        """
        report, reported_at = self.pipeline_report, self.pipeline_reported_at
        if report is None or reported_at is None:
            return PipelineStatusResponse(status="unknown")

        age = max(0.0, (time.monotonic() if now is None else now) - reported_at)
        status: Literal["ok", "degraded", "offline"]
        if age > report.interval_s * PIPELINE_MISSED_REPORTS_OFFLINE:
            status = "offline"
        elif report.problems:
            status = "degraded"
        else:
            status = "ok"
        return PipelineStatusResponse(
            status=status,
            language=report.language,
            # Problems from a pipeline that has gone quiet are history, not state.
            problems=[] if status == "offline" else list(report.problems),
            seconds_since_report=round(age, 1),
        )

    async def _send_pending_update(self) -> None:
        """Notify operators of the current pending queue."""
        await self.connection_manager.send_to_operator(
            {
                "type": "pending_update",
                "data": {
                    "pending": [ref.model_dump() for ref in self.pending_references],
                    "count": len(self.pending_references),
                },
            }
        )

    async def _send_display_reference(self, ref: BibleReference) -> None:
        """Notify displays to show a reference.

        The only path that puts a reference on a projector. Its callers are the
        two operator-authorized ways to change the display: approving a queued
        detection (``approve_pending``) and an operator displaying a passage they
        picked by hand (``display_manual_passage``). Nothing else — and in
        particular no unapproved detection — can reach this method.
        """
        await self.connection_manager.send_to_display(
            {"type": "reference", "data": ref.model_dump()}
        )

    async def _send_display_clear(self) -> None:
        """Notify displays to clear."""
        await self.connection_manager.send_to_display({"type": "clear", "data": {}})

    async def send_display_settings(self, websocket: WebSocket | None = None) -> None:
        """Send the current display settings to displays (or one specific client).

        Displays are presentation-only: settings are chosen in the operator
        console and pushed here, so every projector shows the same passage size.
        """
        message = {
            "type": "display_settings",
            "data": self.display_settings.model_dump(),
        }
        if websocket:
            await self.connection_manager._safe_send(websocket, message)
        else:
            await self.connection_manager.send_to_display(message)

    async def set_verse_limit(self, verse_limit: int | str) -> bool:
        """Set how many verses displays show at once. Returns False if invalid."""
        if verse_limit not in ALLOWED_VERSE_LIMITS:
            return False
        self.display_settings = self.display_settings.model_copy(
            update={"verse_limit": verse_limit}
        )
        await self.send_display_settings()
        logger.info(f"Display verse limit set to {verse_limit}")
        return True

    async def set_display_theme(self, theme: str) -> bool:
        """Set the display background theme. Returns False if invalid."""
        if theme not in ALLOWED_DISPLAY_THEMES:
            return False
        self.display_settings = self.display_settings.model_copy(update={"theme": theme})
        await self.send_display_settings()
        logger.info(f"Display theme set to {theme}")
        return True

    async def set_auto_mode(self, auto_mode: object) -> bool:
        """Choose which new detections are displayed without operator action.

        Returns False for an unknown mode. Only references received from now on
        are affected: anything already pending stays pending, so flipping this
        switch can never silently drain the queue or put an unreviewed detection
        on screen. Persistence is best effort; the in-memory value applies even
        when the settings file cannot be written.
        """
        if not is_auto_mode(auto_mode):
            return False
        self.auto_mode = auto_mode
        self.settings_store.save_auto_mode(auto_mode)
        logger.info(f"Auto-approve mode set to '{auto_mode}'")
        return True

    def should_auto_approve(self, ref: BibleReference, auto_mode: AutoMode | None = None) -> bool:
        """Whether the given mode auto-approves this reference's confidence.

        Defaults to the current mode; ``add_reference`` passes the mode captured
        at arrival so a concurrent ``set_auto_mode`` cannot change how an already
        arrived reference is handled.
        """
        return ref.confidence in AUTO_APPROVE_CONFIDENCES[auto_mode or self.auto_mode]

    async def send_operator_state_snapshot(self, websocket: WebSocket | None = None) -> None:
        """Send complete state snapshot to a specific operator or all operators."""
        snapshot = {
            "type": "state_snapshot",
            "data": {
                "current_display": self.current_display.model_dump()
                if self.current_display
                else None,
                "pending": [ref.model_dump() for ref in self.pending_references],
                "history": [ref.model_dump() for ref in self.reference_history[:20]],  # Last 20
                "pending_count": len(self.pending_references),
                "history_count": len(self.reference_history),
                "display_connections": len(self.connection_manager.display_connections),
                "operator_connections": len(self.connection_manager.operator_connections),
                "display_settings": self.display_settings.model_dump(),
                "auto_mode": self.auto_mode,
            },
        }
        if websocket:
            await self.connection_manager._safe_send(websocket, snapshot)
        else:
            await self.connection_manager.send_to_operator(snapshot)

    async def send_display_current_state(self, websocket: WebSocket) -> None:
        """Send current display state to a newly connected display client."""
        if self.current_display:
            await self.connection_manager._safe_send(
                websocket, {"type": "reference", "data": self.current_display.model_dump()}
            )
        else:
            await self.connection_manager._safe_send(websocket, {"type": "clear", "data": {}})

        # Settings follow the content so a fresh display renders the passage
        # immediately rather than at the default verse count first.
        await self.send_display_settings(websocket)

    async def notify_display_connection_status(self) -> None:
        """Tell operators how many display/operator clients are connected.

        Only display connect/disconnect triggers this broadcast; operator
        connect/disconnect does not, so the operator event stream stays
        deterministic: state_snapshot, then action confirmations and
        pending_update events.
        """
        cm = self.connection_manager
        await cm.send_to_operator(
            {
                "type": "connection_status",
                "data": {
                    "display": len(cm.display_connections),
                    "operator": len(cm.operator_connections),
                },
            }
        )

    async def shutdown(self) -> None:
        """Perform cleanup on shutdown: close database connection."""
        self.bible_lookup.close()
        logger.info("Closed database connection.")

    async def add_reference(self, ref: BibleReference) -> None:
        """Add a new reference to pending queue with bounded size.

        When auto-approve is on and the detection's confidence qualifies, the
        reference is approved immediately through ``approve_and_notify`` — the
        same path the operator uses — so it reaches the display exactly the way a
        manual approval does and the single display-send path stays in one place.
        """
        # Enforce queue limit to prevent unbounded memory growth
        if len(self.pending_references) >= config.MAX_PENDING_REFERENCES:
            dropped = self.pending_references.pop(0)
            logger.warning(
                f"Pending queue full ({config.MAX_PENDING_REFERENCES}), "
                f"dropped oldest reference: {dropped.book} {dropped.chapter}:{dropped.verse_start or '?'}"
            )

        # Capture the mode at arrival, before any await: a set_auto_mode that
        # lands while the arrival broadcast is in flight must not retroactively
        # apply to a reference that arrived under the previous mode.
        mode_at_arrival = self.auto_mode

        self.pending_references.append(ref)
        # Notify operators of new pending reference
        await self._send_pending_update()

        # Auto-approval runs after the arrival update, so the message sequence is
        # the manual one: the reference appears in the queue, then the approval
        # confirmation, then the queue without it.
        if self.should_auto_approve(ref, mode_at_arrival):
            logger.info(
                f"Auto-approving {ref.confidence}-confidence reference "
                f"(mode '{mode_at_arrival}'): {ref.book} {ref.chapter}:{ref.verse_start or '?'}"
            )
            await self.approve_and_notify(ref.id)

    async def approve_pending(self, reference_id: str) -> BibleReference | None:
        """Approve and display a pending reference by ID"""
        # Find the reference by ID
        ref_index = None
        for i, ref in enumerate(self.pending_references):
            if ref.id == reference_id:
                ref_index = i
                break

        if ref_index is None:
            return None

        ref = self.pending_references.pop(ref_index)

        self._resolve_verses(ref)

        self.current_display = ref

        # Keep history bounded to prevent unbounded growth
        self.reference_history.insert(0, ref)
        if len(self.reference_history) > config.MAX_HISTORY_REFERENCES:
            self.reference_history = self.reference_history[: config.MAX_HISTORY_REFERENCES]

        # Notify display to show the reference
        await self._send_display_reference(ref)

        # NOTE: operator notification (confirmation, then the updated queue) is
        # issued by approve_and_notify(); see that method for the required order.

        return ref

    async def approve_and_notify(self, reference_id: str) -> BibleReference | None:
        """Approve a pending reference and notify operators.

        The WebSocket handler and the HTTP endpoint both approve through here so
        operators always observe the same deterministic order: the confirmation
        first, then the updated pending queue. Returns the approved reference, or
        ``None`` when no pending reference matched the ID.
        """
        ref = await self.approve_pending(reference_id)
        if ref is None:
            return None

        await self.connection_manager.send_to_operator(
            {"type": "approval_confirmed", "data": {"reference": ref.model_dump()}}
        )
        await self._send_pending_update()
        return ref

    def _resolve_verses(self, ref: BibleReference) -> None:
        """Fill in verse numbers and text for a reference being displayed.

        A chapter-only reference such as ``Luke 1`` is anchored to verse 1 so the
        shown text and the shown reference agree; without this the display shows
        a reference label with no text next to it. Requested ranges are capped at
        ``config.MAX_VERSES_PER_REFERENCE`` so a whole-chapter request cannot send
        an unbounded payload.

        Verse text only ever comes from the database. Any text the reference
        already carries is replaced, so a submitter cannot put words on the
        display that are not the Bible text, and the label's range end is trimmed
        to the last verse that exists so label and text agree.
        """
        if ref.verse_start is None:
            ref.verse_start = 1

        ref.text = ""
        ref.verses = []
        if not self.bible_lookup.is_loaded():
            return

        verse_end = ref.verse_end if ref.verse_end is not None else ref.verse_start
        if verse_end < ref.verse_start:
            verse_end = ref.verse_start
        if verse_end - ref.verse_start + 1 > config.MAX_VERSES_PER_REFERENCE:
            verse_end = ref.verse_start + config.MAX_VERSES_PER_REFERENCE - 1
            logger.warning(
                f"Reference {ref.book} {ref.chapter}:{ref.verse_start}-{ref.verse_end} "
                f"truncated to verse {verse_end} (limit {config.MAX_VERSES_PER_REFERENCE})"
            )
            ref.verse_end = verse_end

        verses = self.bible_lookup.get_verse_range(
            ref.book, ref.chapter, ref.verse_start, verse_end
        )
        ref.verses = [VerseText(verse=verse, text=text) for verse, text in verses]
        if not ref.verses:
            logger.warning(f"No verse text found for {ref.book} {ref.chapter}:{ref.verse_start}")
            return
        ref.text = " ".join(verse.text for verse in ref.verses)
        # "John 3:30-40": the chapter ends at 36, so the label must too.
        if ref.verse_end is not None and ref.verses[-1].verse < ref.verse_end:
            ref.verse_end = ref.verses[-1].verse

    async def display_manual_passage(self, passage: ResolvedPassage) -> BibleReference:
        """Display a passage the operator selected by hand.

        Manual selection is separate from the detection workflow in every way
        that matters: the reference is never added to the pending queue, it
        carries ``source='manual'`` and no confidence (so nothing can auto-approve
        or confuse it with a detection), and it goes out through the same
        authoritative display path an approval uses. The server, not the client,
        supplies the verse text.

        History semantics match an approval — a manually shown passage is part of
        what the audience saw — while ``current_display`` stays the single source
        of truth for what is on screen.
        """
        ref = BibleReference(
            book=passage.book,
            chapter=passage.chapter,
            verse_start=passage.verse_start,
            verse_end=passage.verse_end,
            confidence=None,
            source="manual",
            text=passage.text,
            verses=[VerseText(verse=verse, text=text) for verse, text in passage.verses],
        )

        self.current_display = ref

        self.reference_history.insert(0, ref)
        if len(self.reference_history) > config.MAX_HISTORY_REFERENCES:
            self.reference_history = self.reference_history[: config.MAX_HISTORY_REFERENCES]

        await self._send_display_reference(ref)

        # Confirm to operators (including this one) only after the display has the
        # passage, so the console never claims a passage is live before it is.
        await self.connection_manager.send_to_operator(
            {"type": "display_confirmed", "data": {"reference": ref.model_dump()}}
        )
        return ref

    async def clear_display(self) -> None:
        """Clear the current display"""
        self.current_display = None
        await self._send_display_clear()

    async def clear_and_notify(self) -> None:
        """Clear the display and confirm it to operators."""
        await self.clear_display()
        await self.connection_manager.send_to_operator({"type": "clear_confirmed", "data": {}})

    async def clear_pending(self) -> None:
        """Clear all pending references.

        Only mutates state; clear_pending_and_notify() broadcasts pending_cleared
        and then pending_update so operators observe a deterministic order.
        """
        self.pending_references.clear()

    async def clear_pending_and_notify(self) -> int:
        """Clear the pending queue and notify operators.

        Emits ``pending_cleared`` before the empty ``pending_update`` so the order
        is enforced here rather than at each call site. Returns how many
        references were cleared.
        """
        count = len(self.pending_references)
        await self.clear_pending()
        await self.connection_manager.send_to_operator({"type": "pending_cleared", "data": {}})
        await self._send_pending_update()
        return count
