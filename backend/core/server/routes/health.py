"""Health routes: /healthz and /readyz."""

import logging

from fastapi import APIRouter, HTTPException

from ...config import config
from ...version import version_string
from ..state import AppState

# Kept on the name these routes logged under before they left app.py.
logger = logging.getLogger("core.server.app")


def build_router(app_state: AppState) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict:
        """Basic health check endpoint - returns 200 if server is running."""
        return {"status": "healthy", "version": version_string()}

    @router.get("/readyz")
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

    return router
