#!/usr/bin/env python3
"""
BibleLive Display/Operator Panel System
FastAPI backend with WebSocket for real-time Bible reference display
"""

import asyncio
import json
import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

if __package__:
    from .bible_lookup import BibleLookup
    from .config import config
    from .verse_parser import ReferenceParser, StreamingParser
else:
    from bible_lookup import BibleLookup
    from config import config
    from verse_parser import ReferenceParser, StreamingParser

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Validate configuration on startup
config.validate()

# Use configuration values
DB_PATH = config.DB_PATH
KJV_JSON_PATH = config.KJV_JSON_PATH
HOST = config.HOST
PORT = config.PORT
FRONTEND_DIST = config.frontend_dist
PROJECT_ROOT = config.project_root
STATIC_DIR = config.static_dir


# Authentication dependency
async def verify_operator_token(authorization: Optional[str] = Header(None)) -> None:
    """Verify operator authentication token.

    Raises:
        HTTPException: 401 if authentication is required but token is missing or invalid.
    """
    # If auth is not required, allow all requests
    if not config.REQUIRE_AUTH:
        return

    # If auth is required but no token configured, reject (misconfiguration)
    if not config.OPERATOR_TOKEN:
        logger.error("Authentication required but BIBLELIVE_OPERATOR_TOKEN not set")
        raise HTTPException(
            status_code=500,
            detail="Server misconfiguration: authentication enabled but token not set",
        )

    # Check for authorization header
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Parse Bearer token
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header format. Use: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]

    # Validate token using constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(token, config.OPERATOR_TOKEN):
        logger.warning(
            "Invalid operator token attempted",
            extra={
                "event": "auth_failure",
                "auth_method": "http_bearer",
                "remote_addr": "unknown",  # FastAPI doesn't easily expose this in dependencies
            },
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# Pydantic models for API and WebSocket messages


class BibleReference(BaseModel):
    """A detected Bible reference with stable ID for tracking."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    book: str
    chapter: int
    verse_start: Optional[int] = None
    verse_end: Optional[int] = None
    confidence: Literal["high", "medium", "low"]
    inferred: bool = False
    text: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# Operator actions (incoming WebSocket messages)


class OperatorApproveAction(BaseModel):
    """Action to approve a pending reference by ID."""

    action: Literal["approve"]
    reference_id: str


class OperatorClearDisplayAction(BaseModel):
    """Action to clear the current display."""

    action: Literal["clear"]


class OperatorClearPendingAction(BaseModel):
    """Action to clear the pending queue."""

    action: Literal["clear_pending"]


# HTTP API request/response models


class ReferenceSubmitRequest(BaseModel):
    """Request body for submitting a new reference to the pending queue."""

    book: str = Field(..., min_length=1, description="Bible book name")
    chapter: int = Field(..., gt=0, description="Chapter number")
    verse_start: Optional[int] = Field(None, gt=0, description="Starting verse number")
    verse_end: Optional[int] = Field(None, gt=0, description="Ending verse number")
    confidence: Literal["high", "medium", "low"] = Field(
        ..., description="Detection confidence level"
    )
    inferred: bool = Field(False, description="Whether context was inferred")
    text: str = Field("", description="Optional verse text")


class ReferenceSubmitResponse(BaseModel):
    """Response after successfully submitting a reference."""

    status: Literal["success"]
    message: str
    reference_id: str


class ApprovalRequest(BaseModel):
    """Request body for approving a pending reference."""

    reference_id: str = Field(..., min_length=1, description="UUID of the reference to approve")


class ApprovalResponse(BaseModel):
    """Response after successfully approving a reference."""

    status: Literal["success"]
    message: str
    reference: BibleReference


class ClearResponse(BaseModel):
    """Response after clearing the display."""

    status: Literal["success"]
    message: str


class ClearPendingResponse(BaseModel):
    """Response after clearing the pending queue."""

    status: Literal["success"]
    message: str


class StateResponse(BaseModel):
    """Response containing complete application state."""

    current_display: Optional[BibleReference]
    pending: list[BibleReference]
    history: list[BibleReference]
    pending_count: int
    history_count: int
    display_connections: int
    operator_connections: int


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str


# Connection manager for WebSocket connections
class ConnectionManager:
    """Manage WebSocket connections for display and operator clients."""

    def __init__(self):
        """Initialize the connection manager with empty connection sets."""
        self.active_connections: list[WebSocket] = []
        self.display_connections: set[WebSocket] = set()
        self.operator_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket, client_type: str = "display"):
        """Accept a websocket connection and register it based on client type.

        Args:
            websocket: The WebSocket connection.
            client_type: Either "display" or "operator". Defaults to "display".
        """
        await websocket.accept()
        self.active_connections.append(websocket)
        if client_type == "display":
            self.display_connections.add(websocket)
        elif client_type == "operator":
            self.operator_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        """Remove a websocket from all connection sets.

        Args:
            websocket: The WebSocket connection to remove.
        """
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.display_connections.discard(websocket)
        self.operator_connections.discard(websocket)

    async def _send_to_connections(self, message: dict, connections):
        """Send message to a set/list of connections"""
        if connections:
            await asyncio.gather(
                *[connection.send_text(json.dumps(message)) for connection in connections],
                return_exceptions=True,
            )

    async def _safe_send(self, websocket: WebSocket, message: dict):
        """Safely send a message to a single websocket, logging errors."""
        try:
            await websocket.send_text(json.dumps(message))
        except Exception as e:
            logger.warning(f"Failed to send WebSocket message: {e}")

    async def send_to_display(self, message: dict):
        """Send message to all display connections"""
        await self._send_to_connections(message, self.display_connections)

    async def send_to_operator(self, message: dict):
        """Send message to all operator connections"""
        await self._send_to_connections(message, self.operator_connections)

    async def broadcast(self, message: dict):
        """Send message to all connections"""
        await self._send_to_connections(message, self.active_connections)


# Lifespan handler (replaces deprecated @app.on_event). app_state is defined
# below; it is only referenced here at shutdown time, after module load.
@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await app_state.shutdown()


# Initialize FastAPI app
app = FastAPI(title="BibleLive Display System", lifespan=lifespan)

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


# Initialize Bible lookup and parser (can be overridden for testing)
def create_bible_lookup(db_path: str = DB_PATH) -> BibleLookup:
    """
    Create and initialize a BibleLookup instance.

    Args:
        db_path: Path to the SQLite database file. Defaults to DB_PATH from environment.

    Returns:
        BibleLookup: An initialized BibleLookup instance.
    """
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
        logger.error("   Run: python -m core.import_bible --translation kjv --source data/kjv.json")
        raise
    except Exception as e:
        logger.error(f"Failed to initialize Bible database: {e}")
        raise


bible_lookup = create_bible_lookup()
parser = ReferenceParser(
    validator=bible_lookup.get_validator_hook() if bible_lookup.is_loaded() else None
)
streaming_parser = StreamingParser(parser)


# Application state
class AppState:
    """Manage the application state including pending references, current display, and connections."""

    def __init__(self):
        """Initialize the application state with empty references and connection manager."""
        self.current_display: Optional[BibleReference] = None
        self.pending_references: list[BibleReference] = []
        self.reference_history: list[BibleReference] = []
        self.connection_manager = ConnectionManager()

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

    async def send_operator_state_snapshot(self, websocket: Optional[WebSocket] = None) -> None:
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

    async def cleanup(self) -> None:
        """No-op placeholder kept for API compatibility."""
        pass

    async def shutdown(self) -> None:
        """Perform cleanup on shutdown: close database connection."""
        # Close database connection
        bible_lookup.close()
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

    async def approve_pending(self, reference_id: str) -> Optional[BibleReference]:
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

        # Fetch verse text from BibleLookup if not already present
        if ref.verse_start is not None and bible_lookup.is_loaded() and not ref.text:
            ref.text = bible_lookup.get_verse_text(
                ref.book, ref.chapter, ref.verse_start, ref.verse_end
            )

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


app_state = AppState()


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
    if FRONTEND_DIST.exists() and (FRONTEND_DIST / "display.html").exists():
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
    html_path = FRONTEND_DIST / filename
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
                                "data": {"message": "Missing 'action' field", "action": "unknown"},
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
                                "data": {"message": f"Unknown action: {action}", "action": action},
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
    )


# Serve static files (CSS/JS, and the built frontend bundle under /static/frontend)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    logger.info("Starting BibleLive Display System...")
    logger.info(config.summary())
    logger.info(f"Display page:  http://{HOST}:{PORT}/display")
    logger.info(f"Operator page: http://{HOST}:{PORT}/operator")
    logger.info(f"API docs:      http://{HOST}:{PORT}/docs")
    logger.info(f"Health check: http://{HOST}:{PORT}/healthz")
    logger.info(f"Ready check:   http://{HOST}:{PORT}/readyz")
    logger.info("\nPress Ctrl+C to stop\n")

    uvicorn_config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
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
