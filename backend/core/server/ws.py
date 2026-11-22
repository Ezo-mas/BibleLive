"""WebSocket endpoint, operator authentication and the operator action table."""

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from ..config import config
from ..passage import PassageError, resolve_passage
from .auth import token_matches
from .models import (
    AutoModeConfirmedData,
    FollowAutoConfirmedData,
    OperatorAction,
    OperatorAutoModeConfirmed,
    OperatorDisplaySettingsConfirmed,
    OperatorError,
    OperatorFollowAutoConfirmed,
    StreamOverlaySettings,
    WebSocketErrorData,
)
from .settings_store import ALLOWED_AUTO_MODES
from .state import NO_TEXT_MESSAGE, ApprovalRefusal, AppState

# Kept on the name this code logged under before it left app.py.
logger = logging.getLogger("core.server.app")


_OPERATOR_ACTION_ADAPTER: TypeAdapter[OperatorAction] = TypeAdapter(OperatorAction)


def _validation_message(error: ValidationError) -> str:
    """Render a pydantic ValidationError as one line for the operator socket."""
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in error.errors()
    )


async def _ws_error(websocket: WebSocket, app_state: AppState, action: str, message: str) -> None:
    """Send a structured error to an operator client.

    Every operator-side error shares this ``{type, data: {message, action}}``
    envelope; building it in one place keeps the wire shape identical across the
    WebSocket handler's branches.
    """
    await app_state.connection_manager._safe_send(
        websocket,
        OperatorError(data=WebSocketErrorData(message=message, action=action)).model_dump(),
    )


async def _handle_approve(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Approve a pending reference (operator action ``approve``)."""
    reference_id = message.get("reference_id")
    if not reference_id:
        await _ws_error(websocket, app_state, "approve", "Missing reference_id")
        return

    try:
        ref = await app_state.approve_and_notify(
            reference_id,
            on_no_text=lambda: _ws_error(websocket, app_state, "approve", NO_TEXT_MESSAGE),
        )
        if ref is ApprovalRefusal.NO_TEXT:
            return
        if ref:
            logger.info(
                f"Reference approved via WebSocket: {ref.book} {ref.chapter}:{ref.verse_start or '?'}"
            )
        else:
            await _ws_error(
                websocket, app_state, "approve", "Reference not found or already processed"
            )
    except Exception as e:
        logger.error(f"Error approving reference: {e}", exc_info=True)
        await _ws_error(websocket, app_state, "approve", "Internal error processing approval")


async def _handle_discard_pending(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Discard one pending reference (operator action ``discard_pending``)."""
    try:
        if not await app_state.discard_pending(message["reference_id"]):
            await _ws_error(
                websocket, app_state, "discard_pending", "Reference not found or already processed"
            )
    except Exception as e:
        logger.error(f"Error discarding pending reference: {e}", exc_info=True)
        await _ws_error(
            websocket, app_state, "discard_pending", "Internal error discarding pending reference"
        )


async def _handle_clear(
    websocket: WebSocket, app_state: AppState, _message: dict[str, Any]
) -> None:
    """Clear the display (operator action ``clear``)."""
    try:
        await app_state.clear_and_notify()
        logger.info("Display cleared via WebSocket")
    except Exception as e:
        logger.error(f"Error clearing display: {e}", exc_info=True)
        await _ws_error(websocket, app_state, "clear", "Internal error clearing display")


async def _handle_clear_pending(
    websocket: WebSocket, app_state: AppState, _message: dict[str, Any]
) -> None:
    """Clear the pending queue (operator action ``clear_pending``)."""
    try:
        count = await app_state.clear_pending_and_notify()
        logger.info(f"Cleared {count} pending references via WebSocket")
    except Exception as e:
        logger.error(f"Error clearing pending: {e}", exc_info=True)
        await _ws_error(
            websocket, app_state, "clear_pending", "Internal error clearing pending queue"
        )


async def _handle_clear_history(
    websocket: WebSocket, app_state: AppState, _message: dict[str, Any]
) -> None:
    """Clear display history (operator action ``clear_history``)."""
    try:
        count = await app_state.clear_history_and_notify()
        logger.info(f"Cleared {count} history references via WebSocket")
    except Exception as e:
        logger.error(f"Error clearing history: {e}", exc_info=True)
        await _ws_error(websocket, app_state, "clear_history", "Internal error clearing history")


async def _handle_set_verse_limit(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Change how many verses displays show (operator action ``set_verse_limit``)."""
    verse_limit = message.get("verse_limit")
    if verse_limit is None or not await app_state.set_verse_limit(verse_limit):
        await _ws_error(
            websocket,
            app_state,
            "set_verse_limit",
            "Invalid verse_limit; expected one of 3, 5, 10, 'all'",
        )
        return

    # Confirm to operators (including this one) so their controls reflect the
    # value displays are now using.
    await app_state.connection_manager.send_to_operator(
        OperatorDisplaySettingsConfirmed(data=app_state.display_settings).model_dump()
    )


async def _handle_set_display_theme(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Change the display theme (operator action ``set_display_theme``)."""
    theme = message.get("theme")
    if theme is None or not await app_state.set_display_theme(theme):
        await _ws_error(
            websocket, app_state, "set_display_theme", "Invalid theme; expected 'dark' or 'light'"
        )
        return

    # Confirm to operators (including this one) so their controls reflect the
    # value displays are now using.
    await app_state.connection_manager.send_to_operator(
        OperatorDisplaySettingsConfirmed(data=app_state.display_settings).model_dump()
    )


async def _handle_set_clear_mode(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Change the clear mode (operator action ``set_clear_mode``)."""
    clear_mode = message.get("clear_mode")
    if clear_mode is None or not await app_state.set_clear_mode(clear_mode):
        await _ws_error(
            websocket,
            app_state,
            "set_clear_mode",
            "Invalid clear mode; expected 'idle' or 'blank'",
        )
        return

    # Confirm to every operator, just like the display theme.
    await app_state.connection_manager.send_to_operator(
        OperatorDisplaySettingsConfirmed(data=app_state.display_settings).model_dump()
    )


async def _handle_set_auto_clear(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Persist the auto-clear duration after the action has been validated."""
    if not await app_state.set_auto_clear(message["seconds"]):
        await _ws_error(websocket, app_state, "set_auto_clear", "Could not save auto-clear setting")
        return
    await app_state.connection_manager.send_to_operator(
        OperatorDisplaySettingsConfirmed(data=app_state.display_settings).model_dump()
    )


async def _handle_set_stream_overlay(
    _websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Change the OBS overlay layout after the action has been validated."""
    await app_state.set_stream_overlay(
        StreamOverlaySettings.model_validate(message["stream_overlay"])
    )
    await app_state.connection_manager.send_to_operator(
        OperatorDisplaySettingsConfirmed(data=app_state.display_settings).model_dump()
    )


async def _handle_set_live(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Put the audience display on air or take it off (operator action ``set_live``)."""
    live = message.get("live")
    if live is None or not await app_state.set_live(live):
        await _ws_error(websocket, app_state, "set_live", "Invalid live; expected true or false")
        return

    # Confirm to every operator, so each console's switch shows what displays obey.
    await app_state.connection_manager.send_to_operator(
        OperatorDisplaySettingsConfirmed(data=app_state.display_settings).model_dump()
    )


async def _handle_display_reference(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Display a passage the operator selected by hand (action ``display_reference``).

    Unlike ``approve``, this never consults the pending queue: the operator names
    the passage directly. The payload is coordinates only, and the book, chapter,
    and verses are re-resolved here against the canonical canon and the server's
    own Bible text — so a client cannot inject verse text, forge a confidence, or
    pass anything off as a detection. Success is confirmed to operators only
    after the display update has been queued.
    """
    try:
        passage = resolve_passage(
            app_state.bible_lookup,
            book=message.get("book"),
            chapter=message.get("chapter"),
            verse_start=message.get("verse_start"),
            verse_end=message.get("verse_end"),
        )
    except PassageError as e:
        logger.warning(f"Rejected manual display request: {e.message}")
        await _ws_error(websocket, app_state, "display_reference", e.message)
        return
    except Exception as e:
        logger.error(f"Error resolving manual passage: {e}", exc_info=True)
        await _ws_error(websocket, app_state, "display_reference", "Could not look up that passage")
        return

    try:
        ref = await app_state.display_manual_passage(passage)
    except Exception as e:
        logger.error(f"Error displaying manual passage: {e}", exc_info=True)
        await _ws_error(
            websocket, app_state, "display_reference", "Internal error displaying passage"
        )
        return

    logger.info(
        f"Manual passage displayed via WebSocket: "
        f"{ref.book} {ref.chapter}:{ref.verse_start}-{ref.verse_end}"
    )


async def _handle_set_auto_mode(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Choose which new detections bypass manual approval (action ``set_auto_mode``).

    Only references that arrive after the change are auto-approved; the queue the
    operator can already see is left exactly as it is.
    """
    auto_mode = message.get("auto_mode")
    if auto_mode is None or not await app_state.set_auto_mode(auto_mode):
        expected = ", ".join(f"'{mode}'" for mode in ALLOWED_AUTO_MODES)
        await _ws_error(
            websocket, app_state, "set_auto_mode", f"Invalid auto_mode; expected one of {expected}"
        )
        return

    # Confirm to operators (including this one) so the selector reflects the mode
    # the server will apply to the next detection.
    await app_state.connection_manager.send_to_operator(
        OperatorAutoModeConfirmed(
            data=AutoModeConfirmedData(auto_mode=app_state.auto_mode)
        ).model_dump()
    )


async def _handle_set_follow_auto(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Choose whether follow-along shows the verses a speaker reads on (``set_follow_auto``).

    Applies to the transcripts that arrive after the change. The switch is server-held,
    so every operator console sees the same setting and none of them shows a verse twice.
    With BIBLELIVE_FOLLOW_ALONG off there is nothing to follow, so turning the switch on is
    refused and nothing is saved; turning it off is always accepted.
    """
    follow_auto = message.get("follow_auto")
    if follow_auto is True and not config.FOLLOW_ALONG:
        await _ws_error(
            websocket, app_state, "set_follow_auto", "Follow-along is turned off on this server"
        )
        return
    if follow_auto is None or not await app_state.set_follow_auto(follow_auto):
        await _ws_error(
            websocket, app_state, "set_follow_auto", "Invalid follow_auto; expected true or false"
        )
        return

    # Confirm to operators (including this one) so the toggle reflects the setting the
    # server will apply to the next transcript.
    await app_state.connection_manager.send_to_operator(
        OperatorFollowAutoConfirmed(
            data=FollowAutoConfirmedData(follow_auto=app_state.follow_auto_in_effect)
        ).model_dump()
    )


async def _handle_authenticate(
    _websocket: WebSocket, _app_state: AppState, _message: dict[str, Any]
) -> None:
    """Ignore an ``authenticate`` on a socket that is already let in.

    The console sends its token on every connection without knowing whether the
    server requires it; with auth off, or after the handshake, there is nothing
    left to decide.
    """


# Seconds an operator socket may stay open without sending its token.
OPERATOR_AUTH_TIMEOUT_S = 10.0


async def _authenticate_operator(websocket: WebSocket) -> bool:
    """Accept an operator socket and check its token; close it with 1008 if wrong.

    The token arrives as the first message, ``{"action": "authenticate",
    "token": ...}``, and nowhere else. A ``?token=`` in the URL is ignored (it
    was accepted until 0.2.0): URLs are logged, by uvicorn and by any proxy in
    front, so a client still sending one waits out the timeout and gets 1008.

    The socket is accepted before the check either way, so a refusal reaches the
    browser as close code 1008 — a refusal before the handshake surfaces as a
    generic 1006 and the console cannot tell a wrong token from a dead server.
    """
    await websocket.accept()

    if not config.OPERATOR_TOKEN:
        logger.error("Authentication required but BIBLELIVE_OPERATOR_TOKEN not set")
        await websocket.close(code=1008, reason="Server misconfiguration")
        return False

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), OPERATOR_AUTH_TIMEOUT_S)
        message = json.loads(raw)
    except (TimeoutError, json.JSONDecodeError, WebSocketDisconnect, RuntimeError):
        message = None
    authorized = (
        isinstance(message, dict)
        and message.get("action") == "authenticate"
        and token_matches(message.get("token"))
    )

    if not authorized:
        logger.warning(
            "Unauthorized operator WebSocket connection attempt",
            extra={
                "event": "auth_failure",
                "auth_method": "websocket_message",
                "client_type": "operator",
            },
        )
        # RuntimeError: the client already went away.
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=1008, reason="Unauthorized")
        return False
    return True


def _log_orphaned_action_error(task: "asyncio.Future[None]") -> None:
    """Log an operator action that failed after its endpoint stopped awaiting it."""
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error("Operator action failed after its socket was dropped", exc_info=error)


# Operator WebSocket actions, dispatched by the ``action`` field. Each handler
# emits exactly the messages (including errors) the previous if/elif chain did;
# only the organization is new.
_OPERATOR_ACTIONS: dict[str, Any] = {
    "authenticate": _handle_authenticate,
    "approve": _handle_approve,
    "discard_pending": _handle_discard_pending,
    "clear": _handle_clear,
    "clear_pending": _handle_clear_pending,
    "clear_history": _handle_clear_history,
    "set_verse_limit": _handle_set_verse_limit,
    "set_display_theme": _handle_set_display_theme,
    "set_clear_mode": _handle_set_clear_mode,
    "set_auto_clear": _handle_set_auto_clear,
    "set_stream_overlay": _handle_set_stream_overlay,
    "set_live": _handle_set_live,
    "set_auto_mode": _handle_set_auto_mode,
    "set_follow_auto": _handle_set_follow_auto,
    "display_reference": _handle_display_reference,
}


def build_router(app_state: AppState) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/{client_type}")
    async def websocket_endpoint(websocket: WebSocket, client_type: str) -> None:
        """WebSocket endpoint for display and operator clients.

        When authentication is enabled, an operator client's first message must
        be ``{"action": "authenticate", "token": "YOUR_TOKEN"}``; nothing is
        sent to it before that. A ``?token=`` in the URL is ignored.
        """
        if client_type not in ("display", "operator"):
            await websocket.close(code=4000)
            return

        # Authenticate operator connections if required
        accepted = False
        if client_type == "operator" and config.REQUIRE_AUTH:
            if not await _authenticate_operator(websocket):
                return
            accepted = True

        cm = app_state.connection_manager
        await cm.connect(websocket, client_type, accepted=accepted, endpoint=asyncio.current_task())

        # Send initial state to the newly connected client. These calls only enqueue:
        # nothing before the ``try`` below may truly suspend, or a manager cancel
        # (see ``except asyncio.CancelledError``) could land outside it.
        if client_type == "display":
            await app_state.send_display_current_state(websocket)
            # Display connections are broadcast to operators; operator connections
            # are not, so operator streams stay deterministic.
            await app_state.notify_display_connection_status()
        elif client_type == "operator":
            await app_state.send_operator_state_snapshot(websocket)

        try:
            while True:
                # Keep connection alive and handle incoming messages
                try:
                    data = await websocket.receive_text()
                    message = json.loads(data)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON from {client_type} client: {e}")
                    if client_type == "operator":
                        await _ws_error(websocket, app_state, "unknown", "Invalid JSON format")
                    continue

                # Handle messages from operator
                if client_type == "operator":
                    action = message.get("action") if isinstance(message, dict) else None
                    if not action:
                        await _ws_error(websocket, app_state, "unknown", "Missing 'action' field")
                        continue

                    # Unknown actions must keep their explicit "Unknown action"
                    # error, so the action name is checked before validation.
                    handler = _OPERATOR_ACTIONS.get(action) if isinstance(action, str) else None
                    if handler is None:
                        await _ws_error(websocket, app_state, action, f"Unknown action: {action}")
                        continue

                    # Validate the payload against its Operator*Action model so a
                    # malformed field is rejected with a clear message before the
                    # handler runs; the handler still reads the raw payload, so no
                    # pydantic coercion is applied to the values it acts on.
                    try:
                        _OPERATOR_ACTION_ADAPTER.validate_python(message)
                    except ValidationError as e:
                        await _ws_error(websocket, app_state, action, _validation_message(e))
                        continue

                    # Shielded: if this socket is abandoned (below) mid-action, the
                    # action still finishes instead of stopping half-applied.
                    action_task = asyncio.ensure_future(handler(websocket, app_state, message))
                    cm._track(action_task)
                    try:
                        await asyncio.shield(action_task)
                    except asyncio.CancelledError:
                        # Nobody awaits the action any more: still log its failure.
                        action_task.add_done_callback(_log_orphaned_action_error)
                        raise

        except asyncio.CancelledError:
            # The manager cancels this task when it pruned the socket and could
            # not close it (the peer stopped reading); end as a disconnect.
            # Any other cancellation, such as server shutdown, propagates.
            task = asyncio.current_task()
            if not cm.take_abandoned(websocket) or (task is not None and task.uncancel() > 0):
                raise
            logger.info(f"{client_type.capitalize()} client pruned and would not close; dropped")
        except WebSocketDisconnect:
            logger.debug(f"{client_type.capitalize()} client disconnected normally")
        except ConnectionResetError:
            logger.debug(f"{client_type.capitalize()} client connection reset")
        except Exception as e:
            logger.error(f"Unexpected WebSocket error for {client_type} client: {e}", exc_info=True)
        finally:
            cm.disconnect(websocket)
            # Only display disconnections notify operators (mirrors connect behavior).
            if client_type == "display":
                await app_state.notify_display_connection_status()

    return router
