"""Application state management."""

import asyncio
import logging
import sqlite3
import time
from typing import Literal

from fastapi import WebSocket

from ..bible_lookup import BibleLookup
from ..bible_versions import BibleVersion, discover_versions
from ..book_names import BookNames
from ..config import config
from ..passage import PassageError, ResolvedPassage, resolve_passage
from .connections import ConnectionManager
from .follow_along import FollowAlong, VerseIndexes
from .idle_screen import (
    IdleImage,
    IdleScreenError,
    IdleScreenFit,
    IdleScreenImageInfo,
    IdleScreenState,
    ImageKind,
    commit_image_file,
    discard_staged_file,
    new_image_version,
    remove_image_files,
    stage_image_file,
)
from .models import (
    AutoMode,
    BibleReference,
    DisplaySettings,
    FollowAlongHint,
    IdleScreenPicture,
    PipelineHeartbeat,
    PipelineStatusResponse,
    StateResponse,
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

# Follow-along auto-show (``set_follow_auto``) shows a hint by itself only when it starts
# at most this many verses after the passage on screen ends. A bigger jump stays a hint.
FOLLOW_AUTO_MAX_JUMP = 3

# How many history entries the state snapshot carries (the operator's ``state_snapshot``
# message and GET /api/state). The list itself is bounded by ``config.MAX_HISTORY_REFERENCES``.
SNAPSHOT_HISTORY = 20


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
        # Follow-along auto-show: the server shows a qualifying hint itself. Persisted
        # with the auto-approve mode, and off until an operator turns it on.
        self.follow_auto: bool = self.settings_store.load_follow_auto()
        # The idle screen: its fit mode and picture, persisted beside the settings file.
        self.idle_screen_fit: IdleScreenFit = self.settings_store.load_idle_screen_fit()
        self.idle_image: IdleImage | None = self._load_idle_image()
        self._sync_display_idle_image()
        # Serializes idle-screen uploads and clears, which touch the picture file and its record.
        self._idle_lock = asyncio.Lock()
        # The audio pipeline's latest health report and when it arrived
        # (time.monotonic()), or None until the first one.
        self.pipeline_report: PipelineHeartbeat | None = None
        self.pipeline_reported_at: float | None = None
        # Follow-along: verse text per spoken language, and the hint policy with its
        # armed state (which hints were offered, and whether partials are wanted).
        self.verse_indexes = VerseIndexes(self.available_bible_versions)
        self.follow_along = FollowAlong()
        # Book labels (``local_name``) of whichever translation is loaded.
        self.book_names = BookNames()

    def book_label(self, book: str) -> str | None:
        """``book`` named as the loaded translation names it, or None for the canonical name.

        Keyed to the loaded Bible, not the spoken language, so the name on screen
        always belongs to the same translation as the verse text under it.
        """
        return self.book_names.label(book, self.bible_lookup.db_path)

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
            issues=[] if status == "offline" else list(report.issues),
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
        detection (``approve_pending``) and displaying a passage by coordinates
        (``display_manual_passage``) — one the operator picked by hand, or, while the
        operator has follow-along auto-show on, the verse the speaker reads on to
        (``source="follow"``). Nothing else — and in particular no unapproved
        detection — can reach this method.
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

    async def set_follow_auto(self, enabled: object) -> bool:
        """Choose whether follow-along shows the verses a speaker reads on to by itself.

        Returns False unless ``enabled`` is a boolean. Only transcripts received from now
        on are affected: a hint already offered is never shown later. Persistence is best
        effort, like ``set_auto_mode``.
        """
        if not isinstance(enabled, bool):
            return False
        self.follow_auto = enabled
        self.settings_store.save_follow_auto(enabled)
        logger.info(f"Follow-along auto-show set to {'on' if enabled else 'off'}")
        return True

    def _load_idle_image(self) -> IdleImage | None:
        """The persisted idle-screen picture, or None when none is saved or its file is gone.

        A saved record without its file is ignored rather than served as a broken image.
        """
        image = self.settings_store.load_idle_screen_image()
        if image is not None and not image.path_in(self.settings_store.directory).is_file():
            logger.warning(f"Ignoring the saved idle-screen picture: {image.filename} is missing")
            return None
        return image

    def _sync_display_idle_image(self) -> None:
        """Mirror the idle picture and its fit into ``display_settings`` for the displays."""
        picture = (
            None
            if self.idle_image is None
            else IdleScreenPicture(version=self.idle_image.version, fit=self.idle_screen_fit)
        )
        self.display_settings = self.display_settings.model_copy(update={"idle_image": picture})

    def idle_screen_state(self) -> IdleScreenState:
        """The idle screen as the console reads it: the fit mode and a version tag, not a path."""
        image = self.idle_image
        return IdleScreenState(
            fit=self.idle_screen_fit,
            image=None
            if image is None
            else IdleScreenImageInfo(version=image.version, content_type=image.content_type),
        )

    async def set_idle_image(self, kind: ImageKind, data: bytes) -> None:
        """Store ``data`` as the idle-screen picture.

        The bytes are staged in a temporary file, the record is saved, and only then is
        the file moved into place, so a failure at any step raises ``IdleScreenError``
        and leaves the picture on show (file, record and memory) as it was. Writes run
        off the event loop, since a large picture takes a moment to flush to disk. The
        lock keeps two uploads (two consoles) from interleaving: one must not delete the
        file the other has just recorded.
        """
        directory = self.settings_store.directory
        async with self._idle_lock:
            try:
                staged = await asyncio.to_thread(stage_image_file, directory, data)
            except OSError as e:
                logger.error(f"Could not store the idle-screen picture in {directory}: {e}")
                raise IdleScreenError(500, "The idle-screen picture could not be stored.") from e
            previous = self.idle_image
            image = IdleImage(kind=kind, version=new_image_version())
            if not self.settings_store.save_idle_screen_image(image):
                discard_staged_file(staged)
                raise IdleScreenError(500, "The idle-screen picture could not be saved.")
            try:
                commit_image_file(staged, directory, kind)
            except OSError as e:
                logger.error(f"Could not put the idle-screen picture in place in {directory}: {e}")
                self._restore_idle_record(previous)
                raise IdleScreenError(500, "The idle-screen picture could not be stored.") from e
            self.idle_image = image
            # Files of the other formats are stale now. Best effort: the record points at this one.
            remove_image_files(directory, keep=kind)
            self._sync_display_idle_image()
        logger.info(f"Idle-screen picture set ({kind}, {len(data)} bytes)")
        await self.send_display_settings()

    def _restore_idle_record(self, previous: IdleImage | None) -> None:
        """Put back the record of the picture on show after a new one could not be placed."""
        restored = (
            self.settings_store.clear_idle_screen_image()
            if previous is None
            else self.settings_store.save_idle_screen_image(previous)
        )
        if not restored:
            logger.error("Could not restore the previous idle-screen picture record")

    async def clear_idle_image(self) -> None:
        """Remove the idle-screen picture, so the display is blank while idle again.

        The record is removed first; if that cannot be saved, the picture stays as it was.
        """
        async with self._idle_lock:
            if not self.settings_store.clear_idle_screen_image():
                raise IdleScreenError(500, "The idle screen could not be cleared.")
            self.idle_image = None
            remove_image_files(self.settings_store.directory)
            self._sync_display_idle_image()
        logger.info("Idle-screen picture removed")
        await self.send_display_settings()

    async def set_idle_fit(self, fit: IdleScreenFit, broadcast: bool = True) -> None:
        """Choose how the idle-screen picture fills the screen (``logo`` or ``fill``).

        Raises ``IdleScreenError`` when the choice cannot be saved; the mode in memory is
        then unchanged. Displays are told right away unless ``broadcast`` is False, for a
        caller that is about to send the settings itself (an upload that names its fit).
        """
        if not self.settings_store.save_idle_screen_fit(fit):
            raise IdleScreenError(500, "The idle-screen fit could not be saved.")
        self.idle_screen_fit = fit
        self._sync_display_idle_image()
        logger.info(f"Idle-screen fit set to '{fit}'")
        if broadcast:
            await self.send_display_settings()

    @property
    def follow_auto_in_effect(self) -> bool:
        """The operator's switch as the server applies it, and as it reports it.

        Auto-show needs follow-along itself (``BIBLELIVE_FOLLOW_ALONG``). While that is off
        the switch reads as off, but ``follow_auto`` keeps the operator's saved choice, so
        it applies again once follow-along is back on.
        """
        return config.FOLLOW_ALONG and self.follow_auto

    def should_auto_approve(self, ref: BibleReference, auto_mode: AutoMode | None = None) -> bool:
        """Whether the given mode auto-approves this reference's confidence.

        Defaults to the current mode; ``add_reference`` passes the mode captured
        at arrival so a concurrent ``set_auto_mode`` cannot change how an already
        arrived reference is handled.
        """
        return ref.confidence in AUTO_APPROVE_CONFIDENCES[auto_mode or self.auto_mode]

    def state_snapshot(self) -> StateResponse:
        """The complete state an operator console starts from.

        One definition for both the WebSocket ``state_snapshot`` message and GET /api/state.
        """
        return StateResponse(
            current_display=self.current_display,
            pending=self.pending_references,
            history=self.reference_history[:SNAPSHOT_HISTORY],
            pending_count=len(self.pending_references),
            history_count=len(self.reference_history),
            display_connections=len(self.connection_manager.display_connections),
            operator_connections=len(self.connection_manager.operator_connections),
            display_settings=self.display_settings,
            auto_mode=self.auto_mode,
            follow_auto=self.follow_auto_in_effect,
        )

    async def send_operator_state_snapshot(self, websocket: WebSocket | None = None) -> None:
        """Send complete state snapshot to a specific operator or all operators."""
        snapshot = {"type": "state_snapshot", "data": self.state_snapshot().model_dump()}
        # The snapshot replaces the console's state, hint included: resend the
        # hint that still follows on from the passage on screen.
        hint = self.follow_along.last_hint
        if not (hint and self.current_display and hint.display_id == self.current_display.id):
            hint = None
        messages = [snapshot]
        if hint is not None:
            messages.append({"type": "follow_along", "data": hint.model_dump()})
        for message in messages:
            if websocket:
                await self.connection_manager._safe_send(websocket, message)
            else:
                await self.connection_manager.send_to_operator(message)

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

    def available_bible_versions(self) -> dict[str, BibleVersion]:
        """Translations that can be switched to, found next to the configured database."""
        return discover_versions(config.resolve_db_path().parent)

    def active_bible_version(self) -> str | None:
        """The translation id the loaded Bible text reports, if it has metadata."""
        translation = self.bible_lookup.get_metadata().get("translation")
        return str(translation).strip().lower() if translation else None

    def switch_bible_version(self, version: str) -> str:
        """Load another translation and use it for all verse text from now on.

        This only swaps the text source. It does not touch the pending queue: a
        queued detection was validated against the old translation, so callers must
        drop it (``change_bible_version`` does, in the same step as the swap). The
        passage on screen is not re-rendered. The new database
        is opened and checked before the old one is released, so a bad file leaves
        the server on the translation it had.

        Raises:
            KeyError: ``version`` is not an available translation.
            ValueError: the translation's file loads but holds no verses.
        """
        target = self.available_bible_versions().get(version)
        if target is None:
            raise KeyError(version)

        new_lookup = BibleLookup(str(target.path), readonly=config.DB_READONLY)
        if new_lookup.verse_count() == 0:
            new_lookup.close()
            raise ValueError(f"Bible translation '{version}' contains no verses.")

        old_lookup, self.bible_lookup = self.bible_lookup, new_lookup
        old_lookup.close()
        self.settings_store.save_bible_version(version, str(config.resolve_db_path()))
        logger.info(f"Bible version switched to '{version}' ({target.path.name})")
        return version

    async def change_bible_version(self, version: str) -> str:
        """Switch translation (see ``switch_bible_version``) and tell every operator.

        The broadcast is how a second operator tab, or an automatic switch that
        followed a language change, shows the new translation without polling.
        """
        previous = self.active_bible_version()
        self.switch_bible_version(version)
        # A queued detection was validated against the previous translation but is
        # displayed from this one. Translations differ in which verses exist
        # (KJV Acts 8:37 is absent from WEB) and in how they number them (LTB
        # splits 1 Samuel 23-24 differently), so a carried-over reference could
        # show no text or a different verse's text. Dropping the queue is the only
        # check that catches both; the audio keeps detecting into a fresh one.
        # This runs in the same synchronous step as the swap: the broadcast below
        # awaits, and an approval that lands during it must find the queue empty,
        # not a reference checked against the old text.
        dropped = 0
        if self.pending_references and self.active_bible_version() != previous:
            dropped = len(self.pending_references)
            self.pending_references.clear()
        await self.connection_manager.send_to_operator(
            {
                "type": "bible_version_confirmed",
                "data": {
                    "version": self.active_bible_version(),
                    "available": sorted(self.available_bible_versions()),
                },
            }
        )
        if dropped:
            await self.connection_manager.send_to_operator({"type": "pending_cleared", "data": {}})
            await self._send_pending_update()
            logger.info(f"Cleared {dropped} pending reference(s): Bible version changed")
        return version

    def apply_start_language_version(self, language: str | None, saved: str | None) -> None:
        """At startup, open the translation of the language the pipeline starts in.

        Without this a fresh install, or one whose saved translation is the other
        language's default (English chosen at the end of the last service), opens
        on that text while the pipeline listens in Lithuanian. A saved translation
        that is no language's default (WEB) was picked by hand and is kept. Best
        effort, like the runtime switch: a missing translation is logged and skipped.
        """
        if not config.AUTO_BIBLE_VERSION or not language:
            return
        target = config.LANGUAGE_BIBLE_VERSIONS.get(language.strip().lower())
        if not target or target == self.active_bible_version():
            return
        if saved and saved not in config.LANGUAGE_BIBLE_VERSIONS.values():
            return
        try:
            self.switch_bible_version(target)
        except (KeyError, ValueError, OSError, sqlite3.Error) as e:
            logger.warning(f"Not opening Bible version {target!r} for language {language!r}: {e!r}")

    async def apply_language_default_version(self, language: str) -> None:
        """After a spoken-language switch, move to that language's default translation.

        Best effort by design: the language switch has already succeeded, so a
        missing or unreadable translation is logged and skipped, never raised.
        """
        if not config.AUTO_BIBLE_VERSION:
            return
        target = config.LANGUAGE_BIBLE_VERSIONS.get(language.strip().lower())
        if not target or target == self.active_bible_version():
            return
        try:
            await self.change_bible_version(target)
        except (KeyError, ValueError, OSError, sqlite3.Error) as e:
            logger.warning(f"Not switching Bible version for language {language!r}: {e!r}")

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

        ref.book_label = self.book_label(ref.book)
        self._preview_verses(ref)
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

    def _record_history(self, ref: BibleReference) -> None:
        """Put a reference that reached the display at the top of the bounded history."""
        self.reference_history.insert(0, ref)
        if len(self.reference_history) > config.MAX_HISTORY_REFERENCES:
            self.reference_history = self.reference_history[: config.MAX_HISTORY_REFERENCES]

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
        self._record_history(ref)

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

    def _preview_verses(self, ref: BibleReference) -> None:
        """Give a queued detection its verse text, so the operator can read it first.

        A copy is resolved, so the queued reference keeps its own label (a
        chapter-only one stays chapter-only; its preview is the verse approval
        would show). Approval resolves the text again from the database, so this
        is only ever a preview; a translation switch clears the queue, so a
        preview never outlives the translation it was read from.
        """
        preview = ref.model_copy()
        self._resolve_verses(preview)
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
        ref.book_label = self.book_label(ref.book)

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
            book_label=self.book_label(passage.book),
            chapter=passage.chapter,
            verse_start=passage.verse_start,
            verse_end=passage.verse_end,
            confidence=None,
            source=source,
            text=passage.text,
            verses=[VerseText(verse=verse, text=text) for verse, text in passage.verses],
        )

        self.current_display = ref

        self._record_history(ref)

        await self._send_display_reference(ref)

        # Confirm to operators (including this one) only after the display has the
        # passage, so the console never claims a passage is live before it is.
        await self.connection_manager.send_to_operator(
            {"type": "display_confirmed", "data": {"reference": ref.model_dump()}}
        )
        return ref

    async def offer_follow_along(self, transcript: str, language: str) -> bool:
        """Tell operators which verse the speaker has reached after the passage on screen.

        Goes to operators only, never to displays, and only when there is a passage
        on screen. A verse is not offered twice for the same display. With the operator's
        ``follow_auto`` switch on, a hint that qualifies (see :meth:`_follow_auto_passage`)
        is shown by itself through ``display_manual_passage`` instead, and operators get
        its ``display_confirmed``. Returns whether follow-along is armed (see
        :class:`~core.server.follow_along.FollowAlong`).
        """
        display = self.current_display
        if display is None or display.verse_start is None:
            return False
        index = await self.verse_indexes.for_language(language)
        # Loading an index takes a moment; the display may have changed meanwhile.
        hint, armed = self.follow_along.on_transcript(
            index, self.bible_lookup, self.current_display, transcript, time.monotonic()
        )
        if hint is not None:
            passage = self._follow_auto_passage(hint)
            if passage is not None:
                # No await between the checks in _follow_auto_passage and this call, so
                # the display cannot change between the decision and the passage going up.
                logger.info(
                    f"Follow-along showed {passage.book} {passage.chapter}:"
                    f"{passage.verse_start}-{passage.verse_end} by itself"
                )
                await self.display_manual_passage(passage, source="follow")
                return armed
            hint.book_label = self.book_label(hint.book)
            await self.connection_manager.send_to_operator(
                {"type": "follow_along", "data": hint.model_dump()}
            )
        return armed

    def _follow_auto_passage(self, hint: FollowAlongHint) -> ResolvedPassage | None:
        """The passage to show by itself for ``hint``, or None to leave it to the operator.

        Only when the operator's ``follow_auto`` switch is on, the hint follows on from the
        passage on screen right now, and it starts at most ``FOLLOW_AUTO_MAX_JUMP`` verses
        after that passage ends. A bigger jump, and a passage that no longer resolves
        against the canon and the Bible text, stay hints. Synchronous on purpose: the
        caller acts on the answer with no await in between.
        """
        display = self.current_display
        if not self.follow_auto_in_effect or display is None or display.verse_start is None:
            return None
        if display.id != hint.display_id:
            return None
        end = display.verse_end or display.verse_start
        if not end < hint.verse_start <= end + FOLLOW_AUTO_MAX_JUMP:
            return None
        try:
            return resolve_passage(
                self.bible_lookup,
                book=hint.book,
                chapter=hint.chapter,
                verse_start=hint.verse_start,
                verse_end=hint.verse_end,
            )
        except PassageError as e:
            logger.warning(f"Follow-along left a hint to the operator: {e.message}")
            return None

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
