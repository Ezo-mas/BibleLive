"""Operator HTTP routes: approve, clear, clear-pending and state."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..auth import verify_operator_token
from ..models import (
    ApprovalRequest,
    ApprovalResponse,
    ClearPendingResponse,
    ClearResponse,
    ErrorResponse,
    StateResponse,
)
from ..state import NO_TEXT_MESSAGE, ApprovalRefusal, AppState

# Kept on the name these routes logged under before they left app.py.
logger = logging.getLogger("core.server.app")


def build_router(app_state: AppState) -> APIRouter:
    router = APIRouter()

    @router.post(
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
        if ref is ApprovalRefusal.NO_TEXT:
            raise HTTPException(status_code=422, detail=NO_TEXT_MESSAGE)

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

    @router.post(
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

    @router.post(
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

    @router.get(
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
        return app_state.state_snapshot()

    return router
