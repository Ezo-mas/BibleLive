"""FastAPI app factory for BibleLive server."""

import asyncio
import contextlib
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter, ValidationError

from ..bible_lookup import BibleLookup
from ..book_names import lithuanian_search_variants
from ..books import CANONICAL_BOOKS
from ..config import config
from ..passage import PassageError, resolve_passage, validate_detection
from . import language_control
from .auth import token_matches, verify_operator_token
from .log_redaction import install_token_redaction
from .models import (
    ApprovalRequest,
    ApprovalResponse,
    BibleReference,
    BibleVersionRequest,
    BibleVersionResponse,
    BookInfo,
    BooksResponse,
    ClearPendingResponse,
    ClearResponse,
    ErrorResponse,
    LanguageRequest,
    LanguageResponse,
    OperatorAction,
    PassageResponse,
    PipelineHeartbeat,
    PipelineStatusResponse,
    ReferenceSubmitRequest,
    ReferenceSubmitResponse,
    StateResponse,
    TranscriptSubmitRequest,
    TranscriptSubmitResponse,
    UiLanguageResponse,
    VerseText,
)
from .settings_store import ALLOWED_AUTO_MODES
from .state import AppState

logger = logging.getLogger(__name__)


def create_bible_lookup(db_path: str | None = None) -> BibleLookup:
    """Create and initialize a BibleLookup instance.

    Args:
        db_path: Path to the SQLite database file. When omitted, the configured
            ``config.DB_PATH`` is used resolved against the project root, so
            the server finds the same database regardless of the process's
            current working directory. An explicitly passed relative path is
            resolved against the current working directory, as usual.

    Returns:
        BibleLookup: An initialized BibleLookup instance.
    """
    if db_path is None:
        db_path = str(config.resolve_db_path())
    try:
        lookup = BibleLookup(db_path, readonly=config.DB_READONLY)
        metadata = lookup.get_metadata()
        if metadata:
            translation = metadata.get("translation", "unknown")
            logger.info(f"Bible database ready: {translation}, {lookup.book_count()} books")
        else:
            logger.info("Bible database ready (no metadata)")
        return lookup
    except FileNotFoundError as e:
        logger.error(f"Bible database not found: {e}")
        logger.error(
            "   Run: python -m core.import_bible --translation kjv --source data/kjv2.json"
        )
        raise
    except Exception as e:
        logger.error(f"Failed to initialize Bible database: {e}")
        raise


_OPERATOR_ACTION_ADAPTER: TypeAdapter[OperatorAction] = TypeAdapter(OperatorAction)


def _validation_message(error: ValidationError) -> str:
    """Render a pydantic ValidationError as one line for the operator socket."""
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in error.errors()
    )


async def _ws_error(websocket: WebSocket, action: str, message: str) -> None:
    """Send a structured error to an operator client.

    Every operator-side error shares this ``{type, data: {message, action}}``
    envelope; building it in one place keeps the wire shape identical across the
    WebSocket handler's branches.
    """
    await websocket.send_text(
        json.dumps({"type": "error", "data": {"message": message, "action": action}})
    )


async def _handle_approve(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Approve a pending reference (operator action ``approve``)."""
    reference_id = message.get("reference_id")
    if not reference_id:
        await _ws_error(websocket, "approve", "Missing reference_id")
        return

    try:
        ref = await app_state.approve_and_notify(reference_id)
        if ref:
            logger.info(
                f"Reference approved via WebSocket: {ref.book} {ref.chapter}:{ref.verse_start or '?'}"
            )
        else:
            await _ws_error(websocket, "approve", "Reference not found or already processed")
    except Exception as e:
        logger.error(f"Error approving reference: {e}", exc_info=True)
        await _ws_error(websocket, "approve", "Internal error processing approval")


async def _handle_clear(
    websocket: WebSocket, app_state: AppState, _message: dict[str, Any]
) -> None:
    """Clear the display (operator action ``clear``)."""
    try:
        await app_state.clear_and_notify()
        logger.info("Display cleared via WebSocket")
    except Exception as e:
        logger.error(f"Error clearing display: {e}", exc_info=True)
        await _ws_error(websocket, "clear", "Internal error clearing display")


async def _handle_clear_pending(
    websocket: WebSocket, app_state: AppState, _message: dict[str, Any]
) -> None:
    """Clear the pending queue (operator action ``clear_pending``)."""
    try:
        count = await app_state.clear_pending_and_notify()
        logger.info(f"Cleared {count} pending references via WebSocket")
    except Exception as e:
        logger.error(f"Error clearing pending: {e}", exc_info=True)
        await _ws_error(websocket, "clear_pending", "Internal error clearing pending queue")


async def _handle_set_verse_limit(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Change how many verses displays show (operator action ``set_verse_limit``)."""
    verse_limit = message.get("verse_limit")
    if verse_limit is None or not await app_state.set_verse_limit(verse_limit):
        await _ws_error(
            websocket,
            "set_verse_limit",
            "Invalid verse_limit; expected one of 3, 5, 10, 'all'",
        )
        return

    # Confirm to operators (including this one) so their controls reflect the
    # value displays are now using.
    await app_state.connection_manager.send_to_operator(
        {"type": "display_settings_confirmed", "data": app_state.display_settings.model_dump()}
    )


async def _handle_set_display_theme(
    websocket: WebSocket, app_state: AppState, message: dict[str, Any]
) -> None:
    """Change the display theme (operator action ``set_display_theme``)."""
    theme = message.get("theme")
    if theme is None or not await app_state.set_display_theme(theme):
        await _ws_error(websocket, "set_display_theme", "Invalid theme; expected 'dark' or 'light'")
        return

    # Confirm to operators (including this one) so their controls reflect the
    # value displays are now using.
    await app_state.connection_manager.send_to_operator(
        {"type": "display_settings_confirmed", "data": app_state.display_settings.model_dump()}
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
    after the display update has been sent.
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
        await _ws_error(websocket, "display_reference", e.message)
        return
    except Exception as e:
        logger.error(f"Error resolving manual passage: {e}", exc_info=True)
        await _ws_error(websocket, "display_reference", "Could not look up that passage")
        return

    try:
        ref = await app_state.display_manual_passage(passage)
    except Exception as e:
        logger.error(f"Error displaying manual passage: {e}", exc_info=True)
        await _ws_error(websocket, "display_reference", "Internal error displaying passage")
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
            websocket, "set_auto_mode", f"Invalid auto_mode; expected one of {expected}"
        )
        return

    # Confirm to operators (including this one) so the selector reflects the mode
    # the server will apply to the next detection.
    await app_state.connection_manager.send_to_operator(
        {"type": "auto_mode_confirmed", "data": {"auto_mode": app_state.auto_mode}}
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
        await _ws_error(websocket, "set_follow_auto", "Follow-along is turned off on this server")
        return
    if follow_auto is None or not await app_state.set_follow_auto(follow_auto):
        await _ws_error(websocket, "set_follow_auto", "Invalid follow_auto; expected true or false")
        return

    # Confirm to operators (including this one) so the toggle reflects the setting the
    # server will apply to the next transcript.
    await app_state.connection_manager.send_to_operator(
        {"type": "follow_auto_confirmed", "data": {"follow_auto": app_state.follow_auto_in_effect}}
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


async def _authenticate_operator(websocket: WebSocket, query_token: str | None) -> bool:
    """Accept an operator socket and check its token; close it with 1008 if wrong.

    The token arrives as the first message, ``{"action": "authenticate",
    "token": ...}``. ``?token=`` in the URL is still honoured for consoles built
    before that change, but it is deprecated: URLs are logged, by uvicorn (which
    ``log_redaction`` covers) and by any proxy in front (which nothing here can).

    The socket is accepted before the check either way, so a refusal reaches the
    browser as close code 1008 — a refusal before the handshake surfaces as a
    generic 1006 and the console cannot tell a wrong token from a dead server.
    """
    await websocket.accept()

    if not config.OPERATOR_TOKEN:
        logger.error("Authentication required but BIBLELIVE_OPERATOR_TOKEN not set")
        await websocket.close(code=1008, reason="Server misconfiguration")
        return False

    if query_token is not None:
        authorized = token_matches(query_token)
        method = "websocket_query"
    else:
        method = "websocket_message"
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
            extra={"event": "auth_failure", "auth_method": method, "client_type": "operator"},
        )
        # RuntimeError: the client already went away.
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=1008, reason="Unauthorized")
        return False
    return True


# Operator WebSocket actions, dispatched by the ``action`` field. Each handler
# emits exactly the messages (including errors) the previous if/elif chain did;
# only the organization is new.
_OPERATOR_ACTIONS: dict[str, Any] = {
    "authenticate": _handle_authenticate,
    "approve": _handle_approve,
    "clear": _handle_clear,
    "clear_pending": _handle_clear_pending,
    "set_verse_limit": _handle_set_verse_limit,
    "set_display_theme": _handle_set_display_theme,
    "set_auto_mode": _handle_set_auto_mode,
    "set_follow_auto": _handle_set_follow_auto,
    "display_reference": _handle_display_reference,
}


def create_app(bible_lookup: BibleLookup | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        bible_lookup: Optional BibleLookup instance. Created from config if not provided.

    Returns:
        FastAPI: Configured application instance.
    """
    # The operator console's WebSocket carries its token in the query string,
    # which uvicorn would otherwise write to the log on every connection.
    install_token_redaction()

    injected_lookup = bible_lookup is not None
    if bible_lookup is None:
        bible_lookup = create_bible_lookup()

    app_state = AppState(bible_lookup)
    if not injected_lookup:
        # Resume the translation the operator last chose. A saved version that is
        # gone or unreadable falls back to the configured database.
        saved_version = app_state.settings_store.load_bible_version(str(config.resolve_db_path()))
        if saved_version and saved_version != app_state.active_bible_version():
            try:
                app_state.switch_bible_version(saved_version)
            except (KeyError, ValueError, OSError, sqlite3.Error) as e:
                logger.warning(f"Could not restore Bible version {saved_version!r}: {e!r}")
        # ...unless the pipeline starts in a language whose translation that is not.
        app_state.apply_start_language_version(config.START_LANGUAGE, saved_version)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await app_state.shutdown()

    app = FastAPI(title="BibleLive Display System", lifespan=lifespan)
    # Expose the state holder so tests and tooling can inspect/reset it without
    # relying on module-level globals.
    app.state.app_state = app_state

    # Configure CORS if origins are specified
    if config.CORS_ORIGINS:
        origins = [origin.strip() for origin in config.CORS_ORIGINS.split(",")]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )
        logger.info(f"CORS enabled for origins: {origins}")

    # API Routes
    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        """Redirect to display page"""
        return HTMLResponse('<meta http-equiv="refresh" content="0; url=/display">')

    @app.get("/healthz")
    async def healthz() -> dict:
        """Basic health check endpoint - returns 200 if server is running."""
        return {"status": "healthy"}

    @app.get("/readyz")
    async def readyz() -> dict:
        """Readiness check - returns 200 if server is ready to accept requests."""
        checks = {"server": "ok", "database": "unknown", "frontend": "unknown"}

        # Check database connection
        try:
            if app_state.bible_lookup.ping():
                checks["database"] = "ok"
            else:
                checks["database"] = "not_loaded"
        except Exception as e:
            logger.warning(f"Database health check failed: {e}")
            checks["database"] = "error"

        # Check frontend build exists
        if config.frontend_dist.exists() and (config.frontend_dist / "display.html").exists():
            checks["frontend"] = "ok"
        else:
            checks["frontend"] = "not_built"

        # Return 503 if any critical component is not ready. The server is only
        # "ready" when both an imported Bible database and a built frontend are
        # available; anything else would serve a degraded experience silently.
        all_ready = checks["database"] == "ok" and checks["frontend"] == "ok"

        if not all_ready:
            raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})

        return {"status": "ready", "checks": checks}

    def _serve_frontend_page(filename: str) -> HTMLResponse:
        """Serve a built frontend page from static/frontend.

        Args:
            filename: HTML file name inside the frontend build output
            (e.g. "display.html" or "operator.html").

        Returns:
            HTMLResponse with the page contents.

        Raises:
            HTTPException: 503 if the frontend has not been built yet.
        """
        html_path = config.frontend_dist / filename
        if not html_path.is_file():
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Frontend build output not found at '{html_path}'. "
                    "Build it with: cd frontend && npm install && npm run build"
                ),
            )
        return HTMLResponse(html_path.read_text())

    @app.get("/display", response_class=HTMLResponse)
    async def get_display_page() -> HTMLResponse:
        """Serve the display page (built frontend bundle)"""
        return _serve_frontend_page("display.html")

    @app.get("/operator", response_class=HTMLResponse)
    async def get_operator_page() -> HTMLResponse:
        """Serve the operator page (built frontend bundle)"""
        return _serve_frontend_page("operator.html")

    @app.websocket("/ws/{client_type}")
    async def websocket_endpoint(
        websocket: WebSocket, client_type: str, token: str | None = None
    ) -> None:
        """WebSocket endpoint for display and operator clients.

        When authentication is enabled, an operator client's first message must
        be ``{"action": "authenticate", "token": "YOUR_TOKEN"}``; nothing is
        sent to it before that. (``?token=`` in the URL still works but is
        deprecated, because URLs are logged.)
        """
        if client_type not in ("display", "operator"):
            await websocket.close(code=4000)
            return

        # Authenticate operator connections if required
        accepted = False
        if client_type == "operator" and config.REQUIRE_AUTH:
            if not await _authenticate_operator(websocket, token):
                return
            accepted = True

        await app_state.connection_manager.connect(websocket, client_type, accepted=accepted)

        # Send initial state to the newly connected client
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
                        await _ws_error(websocket, "unknown", "Invalid JSON format")
                    continue

                # Handle messages from operator
                if client_type == "operator":
                    action = message.get("action")
                    if not action:
                        await _ws_error(websocket, "unknown", "Missing 'action' field")
                        continue

                    # Unknown actions must keep their explicit "Unknown action"
                    # error, so the action name is checked before validation.
                    handler = _OPERATOR_ACTIONS.get(action) if isinstance(action, str) else None
                    if handler is None:
                        await _ws_error(websocket, action, f"Unknown action: {action}")
                        continue

                    # Validate the payload against its Operator*Action model so a
                    # malformed field is rejected with a clear message before the
                    # handler runs; the handler still reads the raw payload, so no
                    # pydantic coercion is applied to the values it acts on.
                    try:
                        _OPERATOR_ACTION_ADAPTER.validate_python(message)
                    except ValidationError as e:
                        await _ws_error(websocket, action, _validation_message(e))
                        continue

                    await handler(websocket, app_state, message)

        except WebSocketDisconnect:
            logger.debug(f"{client_type.capitalize()} client disconnected normally")
        except ConnectionResetError:
            logger.debug(f"{client_type.capitalize()} client connection reset")
        except Exception as e:
            logger.error(f"Unexpected WebSocket error for {client_type} client: {e}", exc_info=True)
        finally:
            app_state.connection_manager.disconnect(websocket)
            # Only display disconnections notify operators (mirrors connect behavior).
            if client_type == "display":
                await app_state.notify_display_connection_status()

    # HTTP API endpoints for integration with reference parser and operator

    @app.post(
        "/api/reference",
        response_model=ReferenceSubmitResponse,
        status_code=201,
        responses={
            201: {"description": "Reference successfully queued"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            404: {"model": ErrorResponse, "description": "No such book, chapter, or verse"},
            422: {"model": ErrorResponse, "description": "Invalid reference data"},
            503: {"model": ErrorResponse, "description": "No Bible text loaded"},
        },
    )
    async def receive_reference(
        reference: ReferenceSubmitRequest, _: None = Depends(verify_operator_token)
    ) -> ReferenceSubmitResponse:
        """Receive a detected Bible reference from the audio pipeline and queue it.

        The reference must name a canonical book and a chapter (or first verse)
        that exists in the server's Bible text; anything else is rejected before
        it is queued, because an auto-approve mode can put a queued reference on
        the display without an operator looking at it. Caller-supplied ``text``
        is discarded: verse text only ever comes from the database at approval.
        Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        try:
            canonical_book = validate_detection(
                app_state.bible_lookup,
                book=reference.book,
                chapter=reference.chapter,
                verse_start=reference.verse_start,
                verse_end=reference.verse_end,
            )
        except PassageError as e:
            logger.warning(f"Rejected reference submission: {e.message}")
            raise HTTPException(status_code=e.status_code, detail=e.message)

        try:
            # Convert to BibleReference object (will generate ID and timestamp)
            ref = BibleReference(
                **reference.model_dump(exclude={"book", "text"}), book=canonical_book
            )

            # Add to pending queue
            await app_state.add_reference(ref)

            logger.info(
                f"Reference queued: {ref.book} {ref.chapter}:{ref.verse_start or '?'} (ID: {ref.id[:8]}...)"
            )

            return ReferenceSubmitResponse(
                status="success",
                message="Reference received and queued",
                reference_id=ref.id,
            )
        except ValueError as e:
            logger.warning(f"Invalid reference data: {e}")
            raise HTTPException(status_code=422, detail=f"Invalid reference data: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error receiving reference: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @app.post(
        "/api/transcript",
        response_model=TranscriptSubmitResponse,
        responses={
            200: {"description": "Transcript received; any follow-along hint goes to operators"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            422: {"model": ErrorResponse, "description": "Invalid transcript data"},
        },
    )
    async def receive_transcript(
        request: TranscriptSubmitRequest, _: None = Depends(verify_operator_token)
    ) -> TranscriptSubmitResponse:
        """Match a confident transcript against the verses after the passage on screen.

        Posted by the audio pipeline for each transcript it trusts. The text is used
        for matching only: it is not stored, logged, or forwarded. The reply does not
        say what the match was, only whether follow-along is armed (``follow_along_active``):
        true while a passage with a verse is on screen and the speaker may still be reading
        it, so the pipeline should send partial transcripts of the utterance in progress.
        With BIBLELIVE_FOLLOW_ALONG off the request succeeds, does nothing, and the flag is
        false. Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        follow_along_active = False
        if config.FOLLOW_ALONG:
            try:
                follow_along_active = await app_state.offer_follow_along(
                    request.text, request.language.strip().lower()
                )
            except Exception as e:
                # Best effort: a matching failure must not reach the pipeline as an error.
                logger.error(f"Follow-along matching failed: {e}", exc_info=True)
        return TranscriptSubmitResponse(status="success", follow_along_active=follow_along_active)

    @app.post(
        "/api/approve",
        response_model=ApprovalResponse,
        responses={
            200: {"description": "Reference successfully approved"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
            404: {"model": ErrorResponse, "description": "Reference not found"},
            422: {"model": ErrorResponse, "description": "Invalid request"},
        },
    )
    async def approve_reference(
        request: ApprovalRequest, _: None = Depends(verify_operator_token)
    ) -> ApprovalResponse:
        """Approve a pending reference by ID and display it.

        Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        # Confirms to connected operators and broadcasts the updated queue in one
        # place, matching the WebSocket handler's order exactly.
        ref = await app_state.approve_and_notify(request.reference_id)

        if ref is None:
            logger.warning(f"Approval failed: reference {request.reference_id[:8]}... not found")
            raise HTTPException(
                status_code=404,
                detail=f"Reference not found or already processed: {request.reference_id}",
            )

        logger.info(
            f"Reference approved: {ref.book} {ref.chapter}:{ref.verse_start or '?'} (ID: {ref.id[:8]}...)"
        )

        return ApprovalResponse(
            status="success",
            message="Reference approved and displayed",
            reference=ref,
        )

    @app.post(
        "/api/clear",
        response_model=ClearResponse,
        responses={
            200: {"description": "Display successfully cleared"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
        },
    )
    async def clear_display(_: None = Depends(verify_operator_token)) -> ClearResponse:
        """Clear the current display.

        Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        await app_state.clear_and_notify()
        logger.info("Display cleared")
        return ClearResponse(status="success", message="Display cleared")

    @app.post(
        "/api/clear-pending",
        response_model=ClearPendingResponse,
        responses={
            200: {"description": "Pending queue successfully cleared"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
        },
    )
    async def clear_pending(_: None = Depends(verify_operator_token)) -> ClearPendingResponse:
        """Clear all pending references.

        Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        count = await app_state.clear_pending_and_notify()
        logger.info(f"Cleared {count} pending references")
        return ClearPendingResponse(status="success", message=f"Cleared {count} pending references")

    @app.get(
        "/api/books",
        response_model=BooksResponse,
        responses={
            200: {"description": "The canonical 66-book list"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def list_books() -> BooksResponse:
        """List the canonical books for the operator's passage search.

        Served from the same canon the parser, importer, and database use, so the
        front end never carries a copy that could drift — including each book's
        abbreviation and the name variants the parser accepts, which is what lets
        the search box match ``ge``, ``ps``, or ``canticles`` without a second
        name table on the client. Requires authentication if
        BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        # The names the loaded translation gives its books (none for KJV). The
        # Lithuanian parser's spellings are added only when that translation is the
        # Lithuanian one, so they can never match books named in another language.
        local_names = app_state.book_names.for_database(app_state.bible_lookup.db_path)
        lithuanian = config.LANGUAGE_BIBLE_VERSIONS.get("lt")
        local_variants = (
            lithuanian_search_variants()
            if local_names and lithuanian and app_state.active_bible_version() == lithuanian
            else {}
        )
        return BooksResponse(
            books=[
                BookInfo(
                    name=book.name,
                    chapters=book.chapters,
                    order=book.order,
                    abbrev=book.abbrev,
                    variants=list(book.variants),
                    local_name=local_names.get(book.name),
                    local_variants=local_variants.get(book.name, []),
                )
                for book in CANONICAL_BOOKS
            ]
        )

    @app.get(
        "/api/lookup",
        response_model=PassageResponse,
        responses={
            200: {"description": "Passage resolved to verse text"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
            404: {"model": ErrorResponse, "description": "No such book, chapter, or verse"},
            422: {"model": ErrorResponse, "description": "Invalid passage parameters"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def lookup_passage(
        book: str = Query(..., min_length=1, max_length=100, description="Canonical book name"),
        chapter: int = Query(..., gt=0, le=200, description="Chapter number"),
        verse_start: int | None = Query(None, gt=0, description="First verse"),
        verse_end: int | None = Query(None, gt=0, description="Last verse (inclusive)"),
    ) -> PassageResponse:
        """Resolve a book/chapter/verse passage to display-ready text.

        Read-only: it consults the canonical canon and the server's Bible
        database and returns the verses, so the console can show the operator what
        would be displayed before they stage it. The same resolver backs the
        ``display_reference`` WebSocket action, so a rejected request reads the
        same either way. Requires authentication if BIBLELIVE_REQUIRE_AUTH is
        enabled.
        """
        try:
            passage = resolve_passage(
                app_state.bible_lookup,
                book=book,
                chapter=chapter,
                verse_start=verse_start,
                verse_end=verse_end,
            )
        except PassageError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)

        return PassageResponse(
            book=passage.book,
            chapter=passage.chapter,
            verse_start=passage.verse_start,
            verse_end=passage.verse_end,
            verses=[VerseText(verse=verse, text=text) for verse, text in passage.verses],
            text=passage.text,
        )

    @app.get(
        "/api/language",
        response_model=LanguageResponse,
        responses={
            200: {"description": "The language the audio pipeline is transcribing in"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
            502: {"model": ErrorResponse, "description": "The pipeline answered wrongly"},
            503: {"model": ErrorResponse, "description": "The audio pipeline is not reachable"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def get_spoken_language() -> LanguageResponse:
        """Report the language the audio pipeline is transcribing in.

        The pipeline is a separate process, so this asks it (see
        ``language_control``): a pipeline that is not running is a 503, not a
        guess. Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        try:
            language = await language_control.get_language()
        except language_control.LanguageControlError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)
        return LanguageResponse(language=language)

    @app.post(
        "/api/language",
        response_model=LanguageResponse,
        responses={
            200: {"description": "The pipeline switched; this is the language it confirmed"},
            400: {"model": ErrorResponse, "description": "The pipeline does not support it"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
            422: {"model": ErrorResponse, "description": "Invalid request"},
            502: {"model": ErrorResponse, "description": "The pipeline answered wrongly"},
            503: {"model": ErrorResponse, "description": "The audio pipeline is not reachable"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def set_spoken_language(request: LanguageRequest) -> LanguageResponse:
        """Switch the language the audio pipeline transcribes in, without a restart.

        Forwarded to the pipeline, which validates the value against the
        languages it has models and parsers for and owns the switch itself: the
        utterance being spoken when it flips is discarded, and audio already
        queued keeps the language it was captured in. The reply is the language
        the pipeline confirms, never an echo of the request. Requires
        authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        # Read the current language first: the translation follows a language
        # *change*. Re-sending the language already active (a second console tab,
        # a retry) must not undo a translation the operator chose since.
        try:
            previous_language: str | None = await language_control.get_language()
        except language_control.LanguageControlError:
            previous_language = None  # the switch below reports an unreachable pipeline
        try:
            language = await language_control.set_language(request.language)
        except language_control.LanguageControlError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)
        logger.info(f"Spoken language set to {language!r}")
        if language != previous_language:
            await app_state.apply_language_default_version(language)
        return LanguageResponse(language=language)

    @app.get(
        "/api/bible-version",
        response_model=BibleVersionResponse,
        responses={
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def get_bible_version() -> BibleVersionResponse:
        """Report the active Bible translation and the ones that can be switched to.

        Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        return BibleVersionResponse(
            version=app_state.active_bible_version(),
            available=sorted(app_state.available_bible_versions()),
        )

    @app.post(
        "/api/bible-version",
        response_model=BibleVersionResponse,
        responses={
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
            404: {"model": ErrorResponse, "description": "No such translation"},
            422: {"model": ErrorResponse, "description": "Invalid request"},
            500: {"model": ErrorResponse, "description": "The translation could not be loaded"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def set_bible_version(request: BibleVersionRequest) -> BibleVersionResponse:
        """Switch the Bible translation used for all verse text, without a restart.

        Pending detections carry coordinates only, so they resolve against the new
        translation when approved; the passage already on screen is left as is.
        The choice is persisted and restored on the next start. Requires
        authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        version = request.version.strip().lower()
        try:
            await app_state.change_bible_version(version)
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"Bible translation '{version}' is not available."
            )
        except (ValueError, OSError, sqlite3.Error) as e:
            logger.error(f"Could not switch Bible version to {version!r}: {e!r}")
            raise HTTPException(
                status_code=500, detail=f"Bible translation '{version}' could not be loaded."
            )
        return BibleVersionResponse(
            version=app_state.active_bible_version(),
            available=sorted(app_state.available_bible_versions()),
        )

    @app.get("/api/ui-language", response_model=UiLanguageResponse)
    async def get_ui_language() -> UiLanguageResponse:
        """Report the interface language the launcher chose for the operator page.

        ``lt`` or ``en``; null when the server was not started by the launcher or
        the value is not one of those, and the console then keeps its own default.
        Public, even with BIBLELIVE_REQUIRE_AUTH: the display page reads it too, and
        it returns nothing but this setting.
        """
        return UiLanguageResponse(language=config.UI_LANGUAGE)

    @app.get(
        "/api/state",
        response_model=StateResponse,
        responses={
            200: {"description": "Current application state"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def get_app_state() -> StateResponse:
        """Get current application state including pending queue, history, and connections.

        Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        return StateResponse(
            current_display=app_state.current_display,
            pending=app_state.pending_references,
            history=app_state.reference_history[:20],  # Last 20 items
            pending_count=len(app_state.pending_references),
            history_count=len(app_state.reference_history),
            display_connections=len(app_state.connection_manager.display_connections),
            operator_connections=len(app_state.connection_manager.operator_connections),
            display_settings=app_state.display_settings,
            auto_mode=app_state.auto_mode,
            follow_auto=app_state.follow_auto_in_effect,
        )

    @app.post(
        "/api/pipeline/heartbeat",
        status_code=204,
        responses={
            204: {"description": "Report recorded"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            422: {"model": ErrorResponse, "description": "Invalid report"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def receive_pipeline_heartbeat(report: PipelineHeartbeat) -> None:
        """Record the audio pipeline's periodic health report.

        Token-protected like ``/api/reference``, so only the pipeline can tell
        the operator that detection is healthy. Requires authentication if
        BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        previous = app_state.pipeline_status().status
        app_state.record_pipeline_report(report)
        current = app_state.pipeline_status()
        if current.status != previous:
            if current.problems:
                logger.warning(f"Audio pipeline {current.status}: {'; '.join(current.problems)}")
            else:
                logger.info(f"Audio pipeline {current.status}")

    @app.get(
        "/api/pipeline/status",
        response_model=PipelineStatusResponse,
        responses={
            200: {"description": "Audio pipeline health as last reported"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def get_pipeline_status() -> PipelineStatusResponse:
        """Whether the audio pipeline is reporting, and what it says is broken.

        Polled by the operator console. Requires authentication if
        BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        return app_state.pipeline_status()

    # Serve static files (CSS/JS, and the built frontend bundle under /static/frontend).
    # static/ is generated and gitignored, so it is absent on a fresh checkout (and in CI)
    # until the frontend is built. Mount only when it exists: StaticFiles raises at
    # construction on a missing directory, which would make create_app() fail outright
    # instead of leaving /display and /operator to return their 503 build instructions.
    if config.static_dir.exists():
        app.mount("/static", StaticFiles(directory=config.static_dir), name="static")
    else:
        logger.warning(
            f"WARNING: Static directory not found at {config.static_dir}; "
            f"/static will not be served. Run: cd frontend && npm install && npm run build"
        )

    return app


def main() -> None:
    """Entry point for running the server."""
    config.validate_config()
    logger.info("Starting BibleLive Display System...")
    logger.info(config.summary())
    logger.info(f"Display page:  http://{config.HOST}:{config.PORT}/display")
    logger.info(f"Operator page: http://{config.HOST}:{config.PORT}/operator")
    logger.info(f"API docs:      http://{config.HOST}:{config.PORT}/docs")
    logger.info(f"Health check: http://{config.HOST}:{config.PORT}/healthz")
    logger.info(f"Ready check:   http://{config.HOST}:{config.PORT}/readyz")
    logger.info("\nPress Ctrl+C to stop\n")

    if config.RELOAD:
        # Reload needs an import string so the reloader can re-import the app;
        # a Config built from an app object silently ignores reload.
        # Same factory `make dev` uses. Watch backend/ only: the repo root
        # holds frontend/node_modules, which must not trigger restarts. The
        # path is absolute because uvicorn quietly falls back to watching the
        # whole working directory when a relative one does not exist there.
        uvicorn.run(
            "core.server.app:create_app",
            factory=True,
            host=config.HOST,
            port=config.PORT,
            reload=True,
            reload_dirs=[str(config.backend_root)],
            log_level=config.LOG_LEVEL.lower(),
        )
        return

    uvicorn_config = uvicorn.Config(
        create_app(),
        host=config.HOST,
        port=config.PORT,
        reload=False,
        log_level=config.LOG_LEVEL.lower(),
    )
    server = uvicorn.Server(uvicorn_config)

    # uvicorn installs its own signal handlers for graceful shutdown;
    # the FastAPI "shutdown" event above closes the database connection.
    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        logger.info("Shutting down server...")


if __name__ == "__main__":
    main()
