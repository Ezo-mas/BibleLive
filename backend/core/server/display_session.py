"""On-screen passage, pending queue, history and authorized display paths."""

import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Literal, Protocol

from fastapi import WebSocket

from ..bible_lookup import BibleLookup
from ..config import config
from ..passage import ResolvedPassage
from .connections import ConnectionManager
from .models import (
    AutoMode,
    BibleReference,
    DisplayClear,
    DisplayReference,
    DisplaySettings,
    HistoryClearedData,
    OperatorApprovalConfirmed,
    OperatorClearConfirmed,
    OperatorDisplayConfirmed,
    OperatorHistoryCleared,
    OperatorPendingCleared,
    OperatorPendingUpdate,
    PendingUpdateData,
    ReferenceConfirmedData,
    VerseText,
)
from .runtime_state import RuntimeStateStore
from .service_log import ServiceLog

# kept on the name this code logged under before it left state.py
logger = logging.getLogger("core.server.state")

# History window for state snapshots and history updates. The backing list is
# bounded separately by config.MAX_HISTORY_REFERENCES.
SNAPSHOT_HISTORY = 20


class ApprovalRefusal(Enum):
    """Immutable approval result, used by the event-loop thread only."""

    NO_TEXT = "no_text"


class DisplaySessionOwner(Protocol):
    auto_mode: AutoMode
    display_settings: DisplaySettings
    bible_lookup: BibleLookup
    connection_manager: ConnectionManager
    runtime_state: RuntimeStateStore
    service_log: ServiceLog

    def book_label(self, book: str) -> str | None: ...

    def active_bible_version(self) -> str | None: ...

    def should_auto_approve(
        self, ref: BibleReference, auto_mode: AutoMode | None = None
    ) -> bool: ...

    def _preview_verses(self, ref: BibleReference) -> None: ...

    def _resolve_verses(self, ref: BibleReference) -> None: ...

    def _record_history(self, ref: BibleReference, *, auto_approved: bool = False) -> None: ...

    async def _send_pending_update(self) -> None: ...

    async def _send_display_reference(self, ref: BibleReference) -> None: ...

    async def _send_display_clear(self) -> None: ...

    async def send_display_settings(self, websocket: WebSocket | None = None) -> None: ...

    async def approve_pending(
        self, reference_id: str
    ) -> BibleReference | ApprovalRefusal | None: ...

    async def approve_and_notify(
        self,
        reference_id: str,
        *,
        on_no_text: Callable[[], Awaitable[None]] | None = None,
    ) -> BibleReference | ApprovalRefusal | None: ...

    async def clear_display(self) -> None: ...

    async def clear_pending(self) -> None: ...

    def clear_history(self) -> int: ...


class DisplaySession:
    """Own display state; shared dependencies are looked up on the owner at call time.

    Every internal call to a method with an AppState forwarder goes through the owner,
    as it did when self was AppState, so patches on AppState still reach those calls.
    """

    def __init__(self, owner: DisplaySessionOwner) -> None:
        self._owner = owner
        self.current_display: BibleReference | None = None
        self.pending_references: list[BibleReference] = []
        self.reference_history: list[BibleReference] = []
        self._auto_approval_ids: set[str] = set()

    async def _send_pending_update(self) -> None:
        """Notify operators of the current pending queue."""
        await self._owner.connection_manager.send_to_operator(
            OperatorPendingUpdate(
                data=PendingUpdateData(
                    pending=self.pending_references, count=len(self.pending_references)
                )
            ).model_dump()
        )

    async def _send_display_reference(self, ref: BibleReference) -> None:
        """Notify displays to show a reference.

        The only path that puts a reference on a projector. Its callers are the
        two operator-authorized ways to change the display (plus
        ``_resend_current_display``, which repeats the one already shown): approving a queued
        detection (``approve_pending``) and displaying a passage by coordinates
        (``display_manual_passage``) — one the operator picked by hand, or, while the
        operator has follow-along auto-show on, the verse the speaker reads on to
        (``source="follow"``). Nothing else — and in particular no unapproved
        detection — can reach this method.

        While the operator has the display off air (``display_settings.live`` false)
        nothing is sent: ``current_display`` and the operator preview still change, but
        no passage reaches a display socket until ``set_live`` puts it back on air.
        """
        if not self._owner.display_settings.live:
            return
        await self._owner.connection_manager.send_to_display(
            DisplayReference(data=ref).model_dump()
        )

    async def _send_display_clear(self) -> None:
        """Notify displays to clear."""
        await self._owner.connection_manager.send_to_display(DisplayClear(data={}).model_dump())

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
            self._owner.service_log.emit("queue_overflowed", count=1)
            logger.warning(
                f"Pending queue full ({config.MAX_PENDING_REFERENCES}), "
                f"dropped oldest reference: {dropped.book} {dropped.chapter}:{dropped.verse_start or '?'}"
            )

        # Capture the mode at arrival, before any await: a set_auto_mode that
        # lands while the arrival broadcast is in flight must not retroactively
        # apply to a reference that arrived under the previous mode.
        mode_at_arrival = self._owner.auto_mode

        ref.book_label = self._owner.book_label(ref.book)
        self._owner._preview_verses(ref)
        self.pending_references.append(ref)
        # Notify operators of new pending reference
        await self._owner._send_pending_update()

        # Auto-approval runs after the arrival update, so the message sequence is
        # the manual one: the reference appears in the queue, then the approval
        # confirmation, then the queue without it.
        if self._owner.should_auto_approve(ref, mode_at_arrival):
            logger.info(
                f"Auto-approving {ref.confidence}-confidence reference "
                f"(mode '{mode_at_arrival}'): {ref.book} {ref.chapter}:{ref.verse_start or '?'}"
            )
            self._auto_approval_ids.add(ref.id)
            try:
                await self._owner.approve_and_notify(ref.id)
            finally:
                self._auto_approval_ids.discard(ref.id)

    def _record_history(self, ref: BibleReference, *, auto_approved: bool = False) -> None:
        """Put a reference that reached the display at the top of the bounded history."""
        self.reference_history.insert(0, ref)
        if len(self.reference_history) > config.MAX_HISTORY_REFERENCES:
            self.reference_history = self.reference_history[: config.MAX_HISTORY_REFERENCES]

        translation = None
        try:
            translation = self._owner.active_bible_version()
            self._owner.runtime_state.record(ref, translation)
            # Off air the audience never saw it: keep it in history, but a restarted
            # (and so live) server must not put it on screen by itself.
            if not self._owner.display_settings.live:
                self._owner.runtime_state.clear()
        except Exception:
            logger.warning("Could not record runtime display state", exc_info=True)
        try:
            self._owner.service_log.emit(
                "shown",
                translation=translation,
                book=ref.book,
                chapter=ref.chapter,
                verse_start=ref.verse_start,
                verse_end=ref.verse_end,
                source=ref.source,
                confidence=ref.confidence,
                inferred=ref.inferred,
                auto_approved=auto_approved,
            )
        except Exception:
            logger.warning("Could not log displayed passage", exc_info=True)

    async def approve_pending(self, reference_id: str) -> BibleReference | ApprovalRefusal | None:
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

        self._owner._resolve_verses(ref)
        if not ref.verses:
            auto_approved = ref.id in self._auto_approval_ids
            logger.warning(
                "Refused approval without text: %s %s:%s", ref.book, ref.chapter, ref.verse_start
            )
            try:
                self._owner.service_log.emit(
                    "refused_no_text",
                    translation=self._owner.active_bible_version(),
                    book=ref.book,
                    chapter=ref.chapter,
                    verse_start=ref.verse_start,
                    verse_end=ref.verse_end,
                    source=ref.source,
                    auto_approved=auto_approved,
                )
            except Exception:
                logger.warning("Could not log no-text refusal", exc_info=True)
            return ApprovalRefusal.NO_TEXT

        self.current_display = ref

        # Keep history bounded to prevent unbounded growth
        self._owner._record_history(ref, auto_approved=ref.id in self._auto_approval_ids)

        # Notify display to show the reference
        await self._owner._send_display_reference(ref)

        # NOTE: operator notification (confirmation, then the updated queue) is
        # issued by approve_and_notify(); see that method for the required order.

        return ref

    async def discard_pending(self, reference_id: str) -> bool:
        """Remove one queued reference and notify operators."""
        for i, ref in enumerate(self.pending_references):
            if ref.id == reference_id:
                self.pending_references.pop(i)
                self._owner.service_log.emit(
                    "pending_discarded",
                    reference_id=ref.id,
                    book=ref.book,
                    chapter=ref.chapter,
                    verse_start=ref.verse_start,
                    verse_end=ref.verse_end,
                )
                logger.info(
                    "Pending reference discarded: %s %s:%s (%s)",
                    ref.book,
                    ref.chapter,
                    ref.verse_start,
                    ref.id,
                )
                await self._owner._send_pending_update()
                return True
        return False

    async def approve_and_notify(
        self,
        reference_id: str,
        *,
        on_no_text: Callable[[], Awaitable[None]] | None = None,
    ) -> BibleReference | ApprovalRefusal | None:
        """Approve a pending reference and notify operators.

        The WebSocket handler and the HTTP endpoint both approve through here so
        operators always observe the same deterministic order: the confirmation
        first, then the updated pending queue. Returns the approved reference, or
        ``None`` when no pending reference matched the ID. A no-text refusal
        reports the requesting socket's error before broadcasting the queue.
        """
        ref = await self._owner.approve_pending(reference_id)
        if ref is ApprovalRefusal.NO_TEXT:
            # The requesting socket must see its error before the queue broadcast.
            try:
                if on_no_text is not None:
                    await on_no_text()
            finally:
                await self._owner._send_pending_update()
            return ref
        if ref is None:
            return None

        await self._owner.connection_manager.send_to_operator(
            OperatorApprovalConfirmed(data=ReferenceConfirmedData(reference=ref)).model_dump()
        )
        await self._owner._send_pending_update()
        return ref

    def _preview_verses(self, ref: BibleReference) -> None:
        """Give a queued detection its verse text, so the operator can read it first.

        A copy is resolved, so the queued reference keeps its own label (a
        chapter-only one stays chapter-only; its preview is the verse approval
        would show). Approval resolves the text again from the database, so this
        is only ever a preview; a translation switch clears the queue, so a
        preview never outlives the translation it was read from.
        """
        preview = ref.model_copy()
        self._owner._resolve_verses(preview)
        ref.verses = preview.verses
        ref.text = preview.text

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
        # Labelled as it goes on screen, so the display always reads in the
        # language spoken now, whatever it was when the detection arrived.
        ref.book_label = self._owner.book_label(ref.book)

        ref.text = ""
        ref.verses = []
        if not self._owner.bible_lookup.is_loaded():
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

        verses = self._owner.bible_lookup.get_verse_range(
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

    async def display_manual_passage(
        self, passage: ResolvedPassage, source: Literal["manual", "follow"] = "manual"
    ) -> BibleReference:
        """Display a passage the operator selected by hand, or one follow-along showed.

        Manual selection is separate from the detection workflow in every way
        that matters: the reference is never added to the pending queue, it
        carries its ``source`` and no confidence (so nothing can auto-approve
        or confuse it with a detection), and it goes out through the same
        authoritative display path an approval uses. The server, not the client,
        supplies the verse text.

        ``source='follow'`` is for the follow-along auto-show. Its only caller is
        ``offer_follow_along``, and only while the operator's ``follow_auto`` switch is on.

        History semantics match an approval — a manually shown passage is part of
        what the audience saw — while ``current_display`` stays the single source
        of truth for what is on screen.
        """
        ref = BibleReference(
            book=passage.book,
            book_label=self._owner.book_label(passage.book),
            chapter=passage.chapter,
            verse_start=passage.verse_start,
            verse_end=passage.verse_end,
            confidence=None,
            source=source,
            text=passage.text,
            verses=[VerseText(verse=verse, text=text) for verse, text in passage.verses],
        )

        self.current_display = ref

        self._owner._record_history(ref)

        await self._owner._send_display_reference(ref)

        # Confirm to operators (including this one) after the passage has been
        # queued for the display.
        await self._owner.connection_manager.send_to_operator(
            OperatorDisplayConfirmed(data=ReferenceConfirmedData(reference=ref)).model_dump()
        )
        return ref

    async def set_live(self, live: bool) -> None:
        """Put the audience display on air or take it off (operator action ``set_live``).

        Off air, displays are told ``live: false`` (a black screen) and then cleared, so
        even a display page too old to know the field drops the passage. Back on air,
        the passage on screen — already authorized when the operator or auto-approve
        showed it, and kept server-side as ``current_display`` — is sent first, then the
        settings, so a display never flashes its idle picture in between. The flag
        changes and both sends are enqueued without an await that yields, so no other
        action can interleave (see the architecture guide).
        """
        if self._owner.display_settings.live == live:
            return
        self._owner.display_settings = self._owner.display_settings.model_copy(
            update={"live": live}
        )
        try:
            self._owner.service_log.emit("live_changed", live=live)
        except Exception:
            logger.warning("Could not log live switch", exc_info=True)
        if not live:
            # A restart comes back live, so it must not restore what is on screen now.
            # Back on air the passage is restored again only once a new one is shown.
            try:
                self._owner.runtime_state.clear()
            except Exception:
                logger.warning("Could not record off-air display", exc_info=True)
        if live:
            await self._resend_current_display()
            await self._owner.send_display_settings()
        else:
            await self._owner.send_display_settings()
            await self._owner._send_display_clear()

    async def _resend_current_display(self) -> None:
        """Send displays the passage already on screen, and nothing else.

        Takes no argument on purpose: back on air the only thing it can send is
        ``current_display``, which an approval or a manual show already authorized.
        The door guard allows this method, not ``set_live``.
        """
        if self.current_display is not None:
            await self._owner._send_display_reference(self.current_display)

    async def clear_display(self) -> None:
        """Clear the current display"""
        self.current_display = None
        try:
            self._owner.runtime_state.clear()
        except Exception:
            logger.warning("Could not record cleared display", exc_info=True)
        try:
            self._owner.service_log.emit("cleared")
        except Exception:
            logger.warning("Could not log cleared display", exc_info=True)
        await self._owner._send_display_clear()

    async def clear_and_notify(self) -> None:
        """Clear the display and confirm it to operators."""
        await self._owner.clear_display()
        await self._owner.connection_manager.send_to_operator(
            OperatorClearConfirmed(data={}).model_dump()
        )

    async def clear_pending(self) -> None:
        """Clear all pending references.

        Only mutates state; clear_pending_and_notify() broadcasts pending_cleared
        and then pending_update so operators observe a deterministic order.
        """
        count = len(self.pending_references)
        self.pending_references.clear()
        if count:
            self._owner.service_log.emit("queue_cleared", count=count)

    async def clear_pending_and_notify(self) -> int:
        """Clear the pending queue and notify operators.

        Emits ``pending_cleared`` before the empty ``pending_update`` so the order
        is enforced here rather than at each call site. Returns how many
        references were cleared.
        """
        count = len(self.pending_references)
        await self._owner.clear_pending()
        await self._owner.connection_manager.send_to_operator(
            OperatorPendingCleared(data={}).model_dump()
        )
        await self._owner._send_pending_update()
        return count

    def clear_history(self) -> int:
        """Empty display history and return the number of removed entries."""
        count = len(self.reference_history)
        try:
            self._owner.runtime_state.clear_history()
        except Exception:
            logger.warning("Could not record cleared history", exc_info=True)
        self.reference_history.clear()
        if count:
            self._owner.service_log.emit("history_cleared", count=count)
        return count

    async def clear_history_and_notify(self) -> int:
        """Clear display history and broadcast the resulting list to operators."""
        count = self._owner.clear_history()
        await self._owner.connection_manager.send_to_operator(
            OperatorHistoryCleared(
                data=HistoryClearedData(
                    history=self.reference_history[:SNAPSHOT_HISTORY],
                    history_count=len(self.reference_history),
                )
            ).model_dump()
        )
        return count
