"""Idle-screen picture routes: /api/idle-screen*."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ..auth import verify_operator_token
from ..idle_screen import (
    IdleScreenError,
    IdleScreenFit,
    IdleScreenFitRequest,
    IdleScreenState,
    read_upload,
    sniff_image_kind,
)
from ..models import ErrorResponse
from ..state import AppState


def build_router(app_state: AppState) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/idle-screen",
        response_model=IdleScreenState,
        responses={401: {"model": ErrorResponse, "description": "Unauthorized"}},
        dependencies=[Depends(verify_operator_token)],
    )
    async def get_idle_screen() -> IdleScreenState:
        """Report the idle screen's fit mode and whether a picture is set.

        The picture itself is served by ``GET /api/idle-screen/image``. Requires
        authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        return app_state.idle_screen_state()

    @router.get(
        "/api/idle-screen/image",
        responses={
            200: {"description": "The idle-screen picture, in the format it was detected as"},
            404: {"model": ErrorResponse, "description": "No idle-screen picture is set"},
        },
    )
    async def get_idle_screen_image() -> FileResponse:
        """Serve the picture the display shows while nothing is on screen.

        Public, like the display page that shows it: the display has no token. The
        content type is the one the server detected at upload, and nosniff stops a
        browser from guessing another.
        """
        image = app_state.idle_image
        if image is None:
            raise HTTPException(status_code=404, detail="No idle-screen picture is set.")
        path = image.path_in(app_state.settings_store.directory)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="No idle-screen picture is set.")
        return FileResponse(
            path,
            media_type=image.content_type,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"},
        )

    @router.post(
        "/api/idle-screen",
        response_model=IdleScreenState,
        responses={
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            413: {"model": ErrorResponse, "description": "The picture is too large"},
            415: {"model": ErrorResponse, "description": "Not a PNG, JPEG or WebP picture"},
            422: {"model": ErrorResponse, "description": "Invalid fit"},
            500: {"model": ErrorResponse, "description": "The picture could not be saved"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def upload_idle_screen(
        request: Request, fit: IdleScreenFit | None = None
    ) -> IdleScreenState:
        """Store the request body as the idle-screen picture.

        The body is the picture itself, not a multipart form. Its format comes from its
        first bytes; only PNG, JPEG and WebP are accepted, and the client's content type
        and any file name are ignored. ``fit`` optionally sets the fit mode in the same
        call. Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        try:
            data = await read_upload(request)
            kind = sniff_image_kind(data)
            if kind is None:
                raise IdleScreenError(415, "Only PNG, JPEG and WebP pictures are accepted.")
            # The fit goes first and quietly, so displays get the picture and its fit in
            # one message rather than the new picture with the old fit.
            if fit is not None:
                await app_state.set_idle_fit(fit, broadcast=False)
            try:
                await app_state.set_idle_image(kind, data)
            except IdleScreenError:
                # The fit is already applied, so the displays must not be left out of date.
                if fit is not None:
                    await app_state.send_display_settings()
                raise
        except IdleScreenError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)
        return app_state.idle_screen_state()

    @router.put(
        "/api/idle-screen/fit",
        response_model=IdleScreenState,
        responses={
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            422: {"model": ErrorResponse, "description": "Invalid fit"},
            500: {"model": ErrorResponse, "description": "The fit could not be saved"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def set_idle_screen_fit(request: IdleScreenFitRequest) -> IdleScreenState:
        """Choose how the picture fills the idle screen: ``logo`` (centred) or ``fill``.

        Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        try:
            await app_state.set_idle_fit(request.fit)
        except IdleScreenError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)
        return app_state.idle_screen_state()

    @router.delete(
        "/api/idle-screen",
        response_model=IdleScreenState,
        responses={
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            500: {"model": ErrorResponse, "description": "The picture could not be removed"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def remove_idle_screen() -> IdleScreenState:
        """Remove the idle-screen picture, so the display is blank while idle again.

        Requires authentication if BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        try:
            await app_state.clear_idle_image()
        except IdleScreenError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)
        return app_state.idle_screen_state()

    return router
