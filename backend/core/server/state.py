"""Application state management."""

import logging

from fastapi import WebSocket

from ..bible_lookup import BibleLookup
from ..config import config
from .connections import ConnectionManager
from .models import BibleReference, DisplaySettings, VerseText

logger = logging.getLogger(__name__)

# Verse limits an operator may choose. Kept in step with the operator console's
# options; anything else is rejected so a typo cannot widen or empty the display.
ALLOWED_VERSE_LIMITS: tuple[int | str, ...] = (3, 5, 10, "all")

# Background themes a display may use. Kept in step with the operator console's
# options; anything else is rejected so a bad value cannot blank the projector.
ALLOWED_DISPLAY_THEMES: tuple[str, ...] = ("dark", "light")


class AppState:
    """Manage the application state including pending references, current display, and connections."""

    def __init__(self, bible_lookup: BibleLookup):
        """Initialize the application state with empty references and connection manager."""
        self.current_display: BibleReference | None = None
        self.pending_references: list[BibleReference] = []
        self.reference_history: list[BibleReference] = []
        self.display_settings = DisplaySettings()
        self.connection_manager = ConnectionManager()
        self.bible_lookup = bible_lookup

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
        """Notify displays to show a reference."""
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
        """Add a new reference to pending queue with bounded size."""
        # Enforce queue limit to prevent unbounded memory growth
        if len(self.pending_references) >= config.MAX_PENDING_REFERENCES:
            dropped = self.pending_references.pop(0)
            logger.warning(
                f"Pending queue full ({config.MAX_PENDING_REFERENCES}), "
                f"dropped oldest reference: {dropped.book} {dropped.chapter}:{dropped.verse_start or '?'}"
            )

        self.pending_references.append(ref)
        # Notify operators of new pending reference
        await self._send_pending_update()

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

        # NOTE: the pending_update broadcast is issued by the caller AFTER it
        # sends the action confirmation, so operators observe a deterministic
        # order: confirmation first, then the updated pending queue.

        return ref

    def _resolve_verses(self, ref: BibleReference) -> None:
        """Fill in verse numbers and text for a reference being displayed.

        A chapter-only reference such as ``Luke 1`` is anchored to verse 1 so the
        shown text and the shown reference agree; without this the display shows
        a reference label with no text next to it. Requested ranges are capped at
        ``config.MAX_VERSES_PER_REFERENCE`` so a whole-chapter request cannot send
        an unbounded payload.
        """
        if ref.verse_start is None:
            ref.verse_start = 1

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

        # A caller may have supplied text without verse numbers (audio pipeline).
        if ref.text and ref.verses:
            return

        verses = self.bible_lookup.get_verse_range(
            ref.book, ref.chapter, ref.verse_start, verse_end
        )
        ref.verses = [VerseText(verse=verse, text=text) for verse, text in verses]
        if ref.verses:
            # Trust the database over caller-supplied text: it is validated and cleaned.
            ref.text = " ".join(verse.text for verse in ref.verses)
        elif not ref.text:
            logger.warning(f"No verse text found for {ref.book} {ref.chapter}:{ref.verse_start}")

    async def clear_display(self) -> None:
        """Clear the current display"""
        self.current_display = None
        await self._send_display_clear()

    async def clear_pending(self) -> None:
        """Clear all pending references.

        Only mutates state; the caller broadcasts pending_cleared and then
        pending_update so operators observe a deterministic order.
        """
        self.pending_references.clear()
