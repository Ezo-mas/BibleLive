"""Application state management."""

import asyncio
import contextlib
import logging
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

from fastapi import WebSocket

from ..bible_lookup import BibleLookup
from ..bible_versions import BibleVersion
from ..book_names import BookNames
from ..config import config
from ..passage import PassageError, ResolvedPassage, resolve_passage
from .audience_qr import HIDDEN as HIDDEN_AUDIENCE_QR
from .audience_qr import audience_qr, audience_url
from .bible_version_service import BibleVersions
from .connections import SEND_TIMEOUT, ConnectionManager
from .display_session import SNAPSHOT_HISTORY, DisplaySession
from .display_session import ApprovalRefusal as ApprovalRefusal
from .follow_along import FOLLOW_AUTO_MAX_JUMP, FollowAlong, VerseIndexes
from .idle_screen import IdleImage, IdleScreenFit, IdleScreenState, ImageKind
from .idle_service import IdleScreenService
from .models import (
    AudienceQrData,
    AutoMode,
    BibleReference,
    BibleVersionResponse,
    ConnectionStatusData,
    DisplayAudienceQr,
    DisplayClear,
    DisplayReference,
    DisplaySettings,
    DisplaySettingsMessage,
    FollowAlongHint,
    OperatorBibleVersionConfirmed,
    OperatorConnectionStatus,
    OperatorFollowAlong,
    OperatorPendingCleared,
    OperatorStateSnapshot,
    PipelineHeartbeat,
    PipelineStatusResponse,
    StateResponse,
    StreamOverlaySettings,
    VerseText,
)
from .pipeline_health import PIPELINE_MISSED_REPORTS_OFFLINE as PIPELINE_MISSED_REPORTS_OFFLINE
from .pipeline_health import PipelineHealth
from .runtime_state import RESTORE_DISPLAY_MAX_AGE_S, RuntimeReference, RuntimeStateStore
from .service_log import ServiceLog
from .settings_store import SettingsStore, coerce_auto_mode, is_auto_mode

logger = logging.getLogger(__name__)

# Verse limits an operator may choose. Kept in step with the operator console's
# options; anything else is rejected so a typo cannot widen or empty the display.
ALLOWED_VERSE_LIMITS: tuple[int | str, ...] = (3, 5, 10, "all")

# Background themes a display may use. Kept in step with the operator console's
# options; anything else is rejected so a bad value cannot blank the projector.
ALLOWED_DISPLAY_THEMES: tuple[str, ...] = ("dark", "light")
ALLOWED_CLEAR_MODES: tuple[str, ...] = ("idle", "blank")

# Confidence levels each auto-approve mode accepts. Confidence order is
# low < medium < high, so "medium_and_up" ("Med+" in the console) covers high as
# well. "off" keeps every detection waiting for the operator.
AUTO_APPROVE_CONFIDENCES: dict[AutoMode, frozenset[str]] = {
    "off": frozenset(),
    "high": frozenset({"high"}),
    "medium_and_up": frozenset({"high", "medium"}),
    "all": frozenset({"high", "medium", "low"}),
}

NO_TEXT_MESSAGE = "Reference has no text in the active translation"


class AppState:
    """Manage server state on the startup/event-loop thread.

    This thread owns the queue, display, history, settings and auto-approval IDs.
    Disk workers receive detached data through the runtime and service stores.
    """

    def __init__(
        self, bible_lookup: BibleLookup, settings_store: SettingsStore | None = None
    ) -> None:
        """Initialize the application state with empty references and connection manager.

        Args:
            bible_lookup: Loaded database used to resolve verse text.
            settings_store: Where persisted operator settings live. Defaults to
                ``config.SETTINGS_PATH``; tests pass a temporary file.
        """
        self._display_session = DisplaySession(self)
        self.connection_manager = ConnectionManager()
        self._bible_versions = BibleVersions(self, bible_lookup)
        self.settings_store = (
            settings_store
            if settings_store is not None
            else SettingsStore(config.resolve_settings_path())
        )
        self.display_settings = DisplaySettings(
            verse_limit=self.settings_store.load_verse_limit(),
            theme=self.settings_store.load_theme(),
            clear_mode=self.settings_store.load_clear_mode(),
            stream_overlay=self.settings_store.load_stream_overlay(),
        )
        # The audience QR code on the displays: in memory only, so a restart hides it.
        self.audience_qr: AudienceQrData = HIDDEN_AUDIENCE_QR
        self.runtime_state = RuntimeStateStore(self.settings_store.directory / "runtime_state.json")
        self.service_log = ServiceLog()
        self._pipeline_health = PipelineHealth(self)
        # Precedence: a persisted mode wins over the environment default, and an
        # unknown environment value leaves auto-approval off.
        self.auto_mode: AutoMode = self.settings_store.load_auto_mode(
            default=coerce_auto_mode(config.AUTO_MODE)
        )
        # Follow-along auto-show: the server shows a qualifying hint itself. Persisted
        # with the auto-approve mode, and off until an operator turns it on.
        self.follow_auto: bool = self.settings_store.load_follow_auto()
        self._idle_service = IdleScreenService(self)
        # Follow-along: verse text per spoken language, and the hint policy with its
        # armed state (which hints were offered, and whether partials are wanted).
        self.verse_indexes = VerseIndexes(self.available_bible_versions)
        self.follow_along = FollowAlong()
        # Book labels (``local_name``) of whichever translation is loaded.
        self.book_names = BookNames()

    @property
    def current_display(self) -> BibleReference | None:
        return self._display_session.current_display

    @current_display.setter
    def current_display(self, value: BibleReference | None) -> None:
        self._display_session.current_display = value

    @property
    def pending_references(self) -> list[BibleReference]:
        return self._display_session.pending_references

    @pending_references.setter
    def pending_references(self, value: list[BibleReference]) -> None:
        self._display_session.pending_references = value

    @property
    def reference_history(self) -> list[BibleReference]:
        return self._display_session.reference_history

    @reference_history.setter
    def reference_history(self, value: list[BibleReference]) -> None:
        self._display_session.reference_history = value

    @property
    def _auto_approval_ids(self) -> set[str]:
        return self._display_session._auto_approval_ids

    @_auto_approval_ids.setter
    def _auto_approval_ids(self, value: set[str]) -> None:
        self._display_session._auto_approval_ids = value

    @property
    def bible_lookup(self) -> BibleLookup:
        return self._bible_versions.bible_lookup

    @bible_lookup.setter
    def bible_lookup(self, value: BibleLookup) -> None:
        self._bible_versions.bible_lookup = value

    @property
    def pipeline_report(self) -> PipelineHeartbeat | None:
        return self._pipeline_health.pipeline_report

    @pipeline_report.setter
    def pipeline_report(self, value: PipelineHeartbeat | None) -> None:
        self._pipeline_health.pipeline_report = value

    @property
    def pipeline_reported_at(self) -> float | None:
        return self._pipeline_health.pipeline_reported_at

    @pipeline_reported_at.setter
    def pipeline_reported_at(self, value: float | None) -> None:
        self._pipeline_health.pipeline_reported_at = value

    @property
    def _logged_pipeline_status(self) -> tuple[str, frozenset[str]] | None:
        return self._pipeline_health._logged_pipeline_status

    @_logged_pipeline_status.setter
    def _logged_pipeline_status(self, value: tuple[str, frozenset[str]] | None) -> None:
        self._pipeline_health._logged_pipeline_status = value

    @property
    def idle_image(self) -> IdleImage | None:
        return self._idle_service.idle_image

    @idle_image.setter
    def idle_image(self, value: IdleImage | None) -> None:
        self._idle_service.idle_image = value

    @property
    def idle_screen_fit(self) -> IdleScreenFit:
        return self._idle_service.idle_screen_fit

    @idle_screen_fit.setter
    def idle_screen_fit(self, value: IdleScreenFit) -> None:
        self._idle_service.idle_screen_fit = value

    @property
    def _idle_lock(self) -> asyncio.Lock:
        return self._idle_service._idle_lock

    @_idle_lock.setter
    def _idle_lock(self, value: asyncio.Lock) -> None:
        self._idle_service._idle_lock = value

    def book_label(self, book: str) -> str | None:
        """``book`` named as the loaded translation names it, or None for the canonical name.

        Keyed to the loaded Bible, not the spoken language, so the name on screen
        always belongs to the same translation as the verse text under it.
        """
        return self.book_names.label(book, self.bible_lookup.db_path)

    def record_pipeline_report(self, report: PipelineHeartbeat, now: float | None = None) -> None:
        return self._pipeline_health.record_pipeline_report(report, now)

    def pipeline_status(self, now: float | None = None) -> PipelineStatusResponse:
        return self._pipeline_health.pipeline_status(now)

    def _log_pipeline_status(self, status: str, issue_codes: list[str]) -> None:
        return self._pipeline_health._log_pipeline_status(status, issue_codes)

    def restore_runtime_state(self) -> None:
        """Re-resolve recent recovery coordinates before any sockets can connect.

        Startup and the event loop own this state. No send or queue restoration
        occurs here; connecting clients receive the ordinary state snapshot.
        """
        saved = self.runtime_state.load()
        original = saved.model_dump(mode="json")

        def resolve(record: RuntimeReference) -> BibleReference | None:
            try:
                passage = resolve_passage(
                    self.bible_lookup,
                    record.book,
                    record.chapter,
                    record.verse_start,
                    record.verse_end,
                )
            except (PassageError, OSError, sqlite3.Error) as exc:
                logger.warning("Dropping runtime passage that no longer resolves: %s", exc)
                return None
            return BibleReference(
                book=passage.book,
                book_label=self.book_label(passage.book),
                chapter=passage.chapter,
                verse_start=passage.verse_start,
                verse_end=passage.verse_end,
                source=record.source,
                confidence=record.confidence,
                inferred=record.inferred,
                created_at=datetime.fromtimestamp(record.shown_at, UTC).isoformat(),
                text=passage.text,
                verses=[VerseText(verse=v, text=t) for v, t in passage.verses],
            )

        now = time.time()

        def recent(record: RuntimeReference) -> bool:
            return 0 <= now - record.shown_at < RESTORE_DISPLAY_MAX_AGE_S

        self.reference_history = []
        valid_history = []
        for record in saved.history:
            if not recent(record):
                continue
            ref = resolve(record)
            if ref is not None:
                self.reference_history.append(ref)
                valid_history.append(record)
        saved.history = valid_history
        current = saved.current
        if current is not None:
            if recent(current) and current.translation != self.active_bible_version():
                logger.info(
                    "Not restoring display: saved translation %r differs from active translation %r",
                    current.translation,
                    self.active_bible_version(),
                )
            elif recent(current):
                if valid_history and current == valid_history[0]:
                    self.current_display = self.reference_history[0]
                else:
                    self.current_display = resolve(current)
            if self.current_display is None:
                saved.current = None
        if saved.model_dump(mode="json") != original:
            self.runtime_state.save_snapshot()

    async def _send_pending_update(self) -> None:
        return await self._display_session._send_pending_update()

    async def _send_display_reference(self, ref: BibleReference) -> None:
        return await self._display_session._send_display_reference(ref)

    async def _send_display_clear(self) -> None:
        return await self._display_session._send_display_clear()

    async def send_display_settings(self, websocket: WebSocket | None = None) -> None:
        """Send the current display settings to displays (or one specific client).

        Displays are presentation-only: settings are chosen in the operator
        console and pushed here, so every projector shows the same passage size.
        """
        message = DisplaySettingsMessage(data=self.display_settings).model_dump()
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
        self.settings_store.save_verse_limit(self.display_settings.verse_limit)
        logger.info(f"Display verse limit set to {verse_limit}")
        return True

    async def set_display_theme(self, theme: str) -> bool:
        """Set the display background theme. Returns False if invalid."""
        if theme not in ALLOWED_DISPLAY_THEMES:
            return False
        self.display_settings = self.display_settings.model_copy(update={"theme": theme})
        await self.send_display_settings()
        self.settings_store.save_theme(self.display_settings.theme)
        logger.info(f"Display theme set to {theme}")
        return True

    async def set_clear_mode(self, clear_mode: str) -> bool:
        """Set what displays show after clearing a passage. Returns False if invalid."""
        if clear_mode not in ALLOWED_CLEAR_MODES:
            return False
        self.display_settings = self.display_settings.model_copy(update={"clear_mode": clear_mode})
        await self.send_display_settings()
        self.settings_store.save_clear_mode(self.display_settings.clear_mode)
        logger.info(f"Display clear mode set to {clear_mode}")
        return True

    async def set_stream_overlay(self, stream_overlay: StreamOverlaySettings) -> None:
        """Set the OBS overlay layout for all displays."""
        self.display_settings = self.display_settings.model_copy(
            update={"stream_overlay": stream_overlay}
        )
        await self.send_display_settings()
        self.settings_store.save_stream_overlay(self.display_settings.stream_overlay)
        logger.info("Stream overlay settings updated")

    async def set_live(self, live: object) -> bool:
        """Put the audience display on air or take it off. Returns False if invalid.

        Server-held so a reconnecting display obeys it, and never persisted: a
        restarted server is live, so a restart cannot leave the projector dark unseen.
        """
        if not isinstance(live, bool):
            return False
        await self._display_session.set_live(live)
        logger.info(f"Display {'live' if live else 'off air'}")
        return True

    async def set_audience_qr(self, visible: bool) -> AudienceQrData:
        """Show or hide the audience QR code on every display; returns what is now shown.

        Showing needs an address other devices can reach (see ``audience_url``); without
        one the code stays hidden. The address is worked out again on every show, so a
        new address from the router is picked up by turning the code off and on.
        """
        # The address lookup can block on the OS (a host-name lookup): off the event loop.
        url = await asyncio.to_thread(audience_url, config.HOST, config.PORT) if visible else None
        qr = audience_qr(url) if url else HIDDEN_AUDIENCE_QR
        # Change and broadcast in one step, with no await between (architecture invariant).
        self.audience_qr = qr
        await self.connection_manager.send_to_display(DisplayAudienceQr(data=qr).model_dump())
        logger.info(f"Audience QR code {'shown' if qr.url else 'hidden'}")
        return qr

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
        return self._idle_service._load_idle_image()

    def _sync_display_idle_image(self) -> None:
        return self._idle_service._sync_display_idle_image()

    def idle_screen_state(self) -> IdleScreenState:
        return self._idle_service.idle_screen_state()

    async def set_idle_image(self, kind: ImageKind, data: bytes) -> None:
        return await self._idle_service.set_idle_image(kind, data)

    def _restore_idle_record(self, previous: IdleImage | None) -> None:
        return self._idle_service._restore_idle_record(previous)

    async def clear_idle_image(self) -> None:
        return await self._idle_service.clear_idle_image()

    async def set_idle_fit(self, fit: IdleScreenFit, broadcast: bool = True) -> None:
        return await self._idle_service.set_idle_fit(fit, broadcast)

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
        snapshot = OperatorStateSnapshot(data=self.state_snapshot()).model_dump()
        # The snapshot replaces the console's state, hint included: resend the
        # hint that still follows on from the passage on screen.
        hint = self.follow_along.last_hint
        if not (hint and self.current_display and hint.display_id == self.current_display.id):
            hint = None
        messages = [snapshot]
        if hint is not None:
            messages.append(OperatorFollowAlong(data=hint).model_dump())
        for message in messages:
            if websocket:
                await self.connection_manager._safe_send(websocket, message)
            else:
                await self.connection_manager.send_to_operator(message)

    async def send_display_current_state(self, websocket: WebSocket) -> None:
        """Send current display state to a newly connected display client.

        Off air, a new display gets no passage at all: the settings first (so a page
        with an idle picture goes black at once), then the clear.
        """
        if not self.display_settings.live:
            await self.send_display_settings(websocket)
            await self.connection_manager._safe_send(websocket, DisplayClear(data={}).model_dump())
            await self._send_audience_qr(websocket)
            return
        if self.current_display:
            await self.connection_manager._safe_send(
                websocket, DisplayReference(data=self.current_display).model_dump()
            )
        else:
            await self.connection_manager._safe_send(websocket, DisplayClear(data={}).model_dump())

        # Settings follow the content so a fresh display renders the passage
        # immediately rather than at the default verse count first.
        await self.send_display_settings(websocket)
        await self._send_audience_qr(websocket)

    async def _send_audience_qr(self, websocket: WebSocket) -> None:
        """A newly connected display gets the audience QR code, only while it is shown."""
        if self.audience_qr.url:
            await self.connection_manager._safe_send(
                websocket, DisplayAudienceQr(data=self.audience_qr).model_dump()
            )

    async def notify_display_connection_status(self) -> None:
        """Tell operators how many display/operator clients are connected.

        Only display connect/disconnect triggers this broadcast; operator
        connect/disconnect does not, so the operator event stream stays
        deterministic: state_snapshot, then action confirmations and
        pending_update events.
        """
        cm = self.connection_manager
        await cm.send_to_operator(
            OperatorConnectionStatus(
                data=ConnectionStatusData(
                    display=len(cm.display_connections), operator=len(cm.operator_connections)
                )
            ).model_dump()
        )

    def available_bible_versions(self) -> dict[str, BibleVersion]:
        return self._bible_versions.available_bible_versions()

    def active_bible_version(self) -> str | None:
        return self._bible_versions.active_bible_version()

    def switch_bible_version(self, version: str) -> str:
        return self._bible_versions.switch_bible_version(version)

    def _open_bible_version(self, version: str) -> BibleLookup:
        return self._bible_versions.open_bible_version(version)

    def _install_bible_version(self, version: str, new_lookup: BibleLookup) -> str | None:
        return self._bible_versions.install_bible_version(version, new_lookup)

    async def change_bible_version(self, version: str) -> str:
        """Switch translation (see ``switch_bible_version``) and tell every operator.

        The broadcast is how a second operator tab, or an automatic switch that
        followed a language change, shows the new translation without polling.
        """
        opening_lock = threading.Lock()
        opened: BibleLookup | None = None
        abandoned = False

        def open_candidate() -> BibleLookup:
            nonlocal opened
            lookup = self._open_bible_version(version)
            with opening_lock:
                if abandoned:
                    lookup.close()
                else:
                    opened = lookup
            return lookup

        try:
            new_lookup = await asyncio.to_thread(open_candidate)
        except asyncio.CancelledError:
            # Cancelling to_thread cannot stop its worker. Either reclaim an
            # already-opened result or leave cleanup to the worker, even if the
            # event loop shuts down before that worker finishes.
            with opening_lock:
                abandoned = True
                if opened is not None:
                    opened.close()
            raise
        # Concurrent opens may finish in either order. Each install is atomic and
        # the last install wins; compare against the version active NOW, not the
        # one that was active before the worker started.
        try:
            previous = self._install_bible_version(version, new_lookup)
        except BaseException:
            # Never close a lookup that the failed install already made live.
            if self.bible_lookup is not new_lookup:
                new_lookup.close()
            raise
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
            self.service_log.emit("queue_cleared", count=dropped)
        await self.connection_manager.send_to_operator(
            OperatorBibleVersionConfirmed(
                data=BibleVersionResponse(
                    version=self.active_bible_version(),
                    available=sorted(self.available_bible_versions()),
                )
            ).model_dump()
        )
        if dropped:
            await self.connection_manager.send_to_operator(
                OperatorPendingCleared(data={}).model_dump()
            )
            await self._send_pending_update()
            logger.info(f"Cleared {dropped} pending reference(s): Bible version changed")
        return version

    def apply_start_language_version(self, language: str | None, saved: str | None) -> None:
        return self._bible_versions.apply_start_language_version(language, saved)

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
        """Best-effort bounded flush and listener shutdown, then close the database."""
        try:
            try:
                await self.runtime_state.flush()
            except Exception:
                logger.warning("Could not flush runtime state during shutdown", exc_info=True)
        finally:
            try:
                try:
                    await asyncio.wait_for(self.connection_manager.close(), timeout=SEND_TIMEOUT)
                except TimeoutError:
                    logger.warning("Timed out closing WebSocket connections")
            finally:
                try:
                    loop = asyncio.get_running_loop()
                    stopped = asyncio.Event()

                    def stop_listener() -> None:
                        try:
                            self.service_log.stop()
                        except Exception:
                            logger.warning("Could not stop service log", exc_info=True)
                        finally:
                            # The loop may have closed after a shutdown timeout.
                            with contextlib.suppress(RuntimeError):
                                loop.call_soon_threadsafe(stopped.set)

                    threading.Thread(
                        target=stop_listener, daemon=True, name="service-log-stop"
                    ).start()
                    try:
                        await asyncio.wait_for(stopped.wait(), timeout=2)
                    except TimeoutError:
                        logger.warning("Timed out stopping service log")
                finally:
                    self.bible_lookup.close()
                    logger.info("Closed database connection.")

    async def add_reference(self, ref: BibleReference) -> None:
        return await self._display_session.add_reference(ref)

    def _record_history(self, ref: BibleReference, *, auto_approved: bool = False) -> None:
        return self._display_session._record_history(ref, auto_approved=auto_approved)

    async def approve_pending(self, reference_id: str) -> BibleReference | ApprovalRefusal | None:
        return await self._display_session.approve_pending(reference_id)

    async def discard_pending(self, reference_id: str) -> bool:
        return await self._display_session.discard_pending(reference_id)

    async def approve_and_notify(
        self,
        reference_id: str,
        *,
        on_no_text: Callable[[], Awaitable[None]] | None = None,
    ) -> BibleReference | ApprovalRefusal | None:
        return await self._display_session.approve_and_notify(reference_id, on_no_text=on_no_text)

    def _preview_verses(self, ref: BibleReference) -> None:
        return self._display_session._preview_verses(ref)

    def _resolve_verses(self, ref: BibleReference) -> None:
        return self._display_session._resolve_verses(ref)

    async def display_manual_passage(
        self, passage: ResolvedPassage, source: Literal["manual", "follow"] = "manual"
    ) -> BibleReference:
        return await self._display_session.display_manual_passage(passage, source)

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
                OperatorFollowAlong(data=hint).model_dump()
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
                verse_start=hint.show_from if hint.show_from is not None else hint.verse_start,
                verse_end=hint.verse_end,
            )
        except PassageError as e:
            logger.warning(f"Follow-along left a hint to the operator: {e.message}")
            return None

    async def clear_display(self) -> None:
        return await self._display_session.clear_display()

    async def clear_and_notify(self) -> None:
        return await self._display_session.clear_and_notify()

    async def clear_pending(self) -> None:
        return await self._display_session.clear_pending()

    async def clear_pending_and_notify(self) -> int:
        return await self._display_session.clear_pending_and_notify()

    def clear_history(self) -> int:
        return self._display_session.clear_history()

    async def clear_history_and_notify(self) -> int:
        return await self._display_session.clear_history_and_notify()
