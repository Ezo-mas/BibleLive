"""Bible translation routes: GET/POST /api/bible-version."""

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..auth import verify_operator_token
from ..models import BibleVersionRequest, BibleVersionResponse, ErrorResponse
from ..state import AppState

# Kept on the name these routes logged under before they left app.py.
logger = logging.getLogger("core.server.app")


def build_router(app_state: AppState) -> APIRouter:
    router = APIRouter()

    @router.get(
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

    @router.post(
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

    return router
