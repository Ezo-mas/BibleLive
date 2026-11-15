"""Page routes: /, /display and /operator (the built frontend pages)."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from ...config import config
from ..state import AppState


def build_router(app_state: AppState) -> APIRouter:  # noqa: ARG001
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        """Redirect to display page"""
        return HTMLResponse('<meta http-equiv="refresh" content="0; url=/display">')

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

    @router.get("/display", response_class=HTMLResponse)
    async def get_display_page() -> HTMLResponse:
        """Serve the display page (built frontend bundle)"""
        return _serve_frontend_page("display.html")

    @router.get("/operator", response_class=HTMLResponse)
    async def get_operator_page() -> HTMLResponse:
        """Serve the operator page (built frontend bundle)"""
        return _serve_frontend_page("operator.html")

    return router
