"""FastAPI app factory for BibleLive server."""

import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..bible_lookup import BibleLookup
from ..config import config
from ..verse_parser import ReferenceParser, StreamingParser
from .auth import verify_operator_token
from .models import (
    ApprovalRequest,
    ApprovalResponse,
    BibleReference,
    ClearPendingResponse,
    ClearResponse,
    ErrorResponse,
    ReferenceSubmitRequest,
    ReferenceSubmitResponse,
    StateResponse,
)
from .state import AppState

logger = logging.getLogger(__name__)


def create_bible_lookup(db_path: str = None) -> BibleLookup:
    """Create and initialize a BibleLookup instance.

    Args:
        db_path: Path to the SQLite database file. Defaults to config.DB_PATH.

    Returns:
        BibleLookup: An initialized BibleLookup instance.
    """
    if db_path is None:
        db_path = config.DB_PATH
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


def create_app(
    bible_lookup: Optional[BibleLookup] = None,
    parser: Optional[ReferenceParser] = None,
    streaming_parser: Optional[StreamingParser] = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        bible_lookup: Optional BibleLookup instance. Created from config if not provided.
        parser: Optional ReferenceParser instance. Created from bible_lookup if not provided.
        streaming_parser: Optional StreamingParser instance. Created from parser if not provided.

    Returns:
        FastAPI: Configured application instance.
    """
    if bible_lookup is None:
        bible_lookup = create_bible_lookup()
    if parser is None:
        parser = ReferenceParser(
            validator=bible_lookup.get_validator_hook() if bible_lookup.is_loaded() else None
        )
    if streaming_parser is None:
        streaming_parser = StreamingParser(parser)

    app_state = AppState(bible_lookup)

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
            if bible_lookup.ping():
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
        websocket: WebSocket, client_type: str, token: Optional[str] = None
    ) -> None:
        """WebSocket endpoint for display and operator clients.

        For operator connections when authentication is enabled, provide token as query parameter:
        /ws/operator?token=YOUR_TOKEN
        """
        if client_type not in ("display", "operator"):
            await websocket.close(code=4000)
            return

        # Authenticate operator connections if required
        if client_type == "operator" and config.REQUIRE_AUTH:
            if not config.OPERATOR_TOKEN:
                logger.error("Authentication required but BIBLELIVE_OPERATOR_TOKEN not set")
                await websocket.close(code=1008, reason="Server misconfiguration")
                return

            if not token or not secrets.compare_digest(token, config.OPERATOR_TOKEN):
                logger.warning(
                    "Unauthorized operator WebSocket connection attempt",
                    extra={
                        "event": "auth_failure",
                        "auth_method": "websocket_query",
                        "client_type": "operator",
                    },
                )
                await websocket.close(code=1008, reason="Unauthorized")
                return

        await app_state.connection_manager.connect(websocket, client_type)

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
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "data": {"message": "Invalid JSON format", "action": "unknown"},
                                }
                            )
                        )
                    continue

                # Handle messages from operator
                if client_type == "operator":
                    action = message.get("action")

                    if not action:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "data": {
                                        "message": "Missing 'action' field",
                                        "action": "unknown",
                                    },
                                }
                            )
                        )
                        continue

                    if action == "approve":
                        reference_id = message.get("reference_id")
                        if not reference_id:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "data": {
                                            "message": "Missing reference_id",
                                            "action": "approve",
                                        },
                                    }
                                )
                            )
                            continue

                        try:
                            ref = await app_state.approve_pending(reference_id)
                            if ref:
                                # Confirm to operators first, then broadcast the
                                # updated pending queue (deterministic order).
                                await app_state.connection_manager.send_to_operator(
                                    {
                                        "type": "approval_confirmed",
                                        "data": {"reference": ref.model_dump()},
                                    }
                                )
                                await app_state._send_pending_update()
                                logger.info(
                                    f"Reference approved via WebSocket: {ref.book} {ref.chapter}:{ref.verse_start or '?'}"
                                )
                            else:
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "type": "error",
                                            "data": {
                                                "message": "Reference not found or already processed",
                                                "action": "approve",
                                            },
                                        }
                                    )
                                )
                        except Exception as e:
                            logger.error(f"Error approving reference: {e}", exc_info=True)
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "data": {
                                            "message": "Internal error processing approval",
                                            "action": "approve",
                                        },
                                    }
                                )
                            )

                    elif action == "clear":
                        try:
                            await app_state.clear_display()
                            await app_state.connection_manager.send_to_operator(
                                {"type": "clear_confirmed", "data": {}}
                            )
                            logger.info("Display cleared via WebSocket")
                        except Exception as e:
                            logger.error(f"Error clearing display: {e}", exc_info=True)
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "data": {
                                            "message": "Internal error clearing display",
                                            "action": "clear",
                                        },
                                    }
                                )
                            )

                    elif action == "set_verse_limit":
                        verse_limit = message.get("verse_limit")
                        if not await app_state.set_verse_limit(verse_limit):
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "data": {
                                            "message": (
                                                "Invalid verse_limit; expected one of 3, 5, 10, 'all'"
                                            ),
                                            "action": "set_verse_limit",
                                        },
                                    }
                                )
                            )
                        else:
                            # Confirm to operators (including this one) so their
                            # controls reflect the value displays are now using.
                            await app_state.connection_manager.send_to_operator(
                                {
                                    "type": "display_settings_confirmed",
                                    "data": app_state.display_settings.model_dump(),
                                }
                            )

                    elif action == "set_display_theme":
                        theme = message.get("theme")
                        if not await app_state.set_display_theme(theme):
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "data": {
                                            "message": (
                                                "Invalid theme; expected 'dark' or 'light'"
                                            ),
                                            "action": "set_display_theme",
                                        },
                                    }
                                )
                            )
                        else:
                            # Confirm to operators (including this one) so their
                            # controls reflect the value displays are now using.
                            await app_state.connection_manager.send_to_operator(
                                {
                                    "type": "display_settings_confirmed",
                                    "data": app_state.display_settings.model_dump(),
                                }
                            )

                    elif action == "clear_pending":
                        try:
                            count = len(app_state.pending_references)
                            await app_state.clear_pending()
                            await app_state.connection_manager.send_to_operator(
                                {"type": "pending_cleared", "data": {}}
                            )
                            # Confirm first, then broadcast the (now empty) queue.
                            await app_state._send_pending_update()
                            logger.info(f"Cleared {count} pending references via WebSocket")
                        except Exception as e:
                            logger.error(f"Error clearing pending: {e}", exc_info=True)
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "data": {
                                            "message": "Internal error clearing pending queue",
                                            "action": "clear_pending",
                                        },
                                    }
                                )
                            )

                    else:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "data": {
                                        "message": f"Unknown action: {action}",
                                        "action": action,
                                    },
                                }
                            )
                        )

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
            422: {"model": ErrorResponse, "description": "Invalid reference data"},
        },
    )
    async def receive_reference(reference: ReferenceSubmitRequest) -> ReferenceSubmitResponse:
        """Receive a validated Bible reference from the parser and add to pending queue."""
        try:
            # Convert to BibleReference object (will generate ID and timestamp)
            ref = BibleReference(**reference.model_dump())

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
        ref = await app_state.approve_pending(request.reference_id)

        if ref is None:
            logger.warning(f"Approval failed: reference {request.reference_id[:8]}... not found")
            raise HTTPException(
                status_code=404,
                detail=f"Reference not found or already processed: {request.reference_id}",
            )

        # Confirm the result to connected operators, then broadcast the updated
        # pending queue (same deterministic order as the WebSocket handler).
        await app_state.connection_manager.send_to_operator(
            {
                "type": "approval_confirmed",
                "data": {"reference": ref.model_dump()},
            }
        )
        await app_state._send_pending_update()

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
        await app_state.clear_display()
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
        count = len(app_state.pending_references)
        await app_state.clear_pending()
        # Notify connected operators of the now-empty queue.
        await app_state._send_pending_update()
        logger.info(f"Cleared {count} pending references")
        return ClearPendingResponse(status="success", message=f"Cleared {count} pending references")

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
        )

    # Serve static files (CSS/JS, and the built frontend bundle under /static/frontend)
    app.mount("/static", StaticFiles(directory=config.static_dir), name="static")

    return app


def main() -> None:
    """Entry point for running the server."""
    config.validate()
    logger.info("Starting BibleLive Display System...")
    logger.info(config.summary())
    logger.info(f"Display page:  http://{config.HOST}:{config.PORT}/display")
    logger.info(f"Operator page: http://{config.HOST}:{config.PORT}/operator")
    logger.info(f"API docs:      http://{config.HOST}:{config.PORT}/docs")
    logger.info(f"Health check: http://{config.HOST}:{config.PORT}/healthz")
    logger.info(f"Ready check:   http://{config.HOST}:{config.PORT}/readyz")
    logger.info("\nPress Ctrl+C to stop\n")

    uvicorn_config = uvicorn.Config(
        create_app(),
        host=config.HOST,
        port=config.PORT,
        reload=config.RELOAD,
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
