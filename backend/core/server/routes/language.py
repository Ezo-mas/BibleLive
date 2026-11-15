"""Spoken and interface language routes: /api/language, /api/ui-language."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ...config import config
from .. import language_control
from ..auth import verify_operator_token
from ..models import ErrorResponse, LanguageRequest, LanguageResponse, UiLanguageResponse
from ..state import AppState

# Kept on the name these routes logged under before they left app.py.
logger = logging.getLogger("core.server.app")


def build_router(app_state: AppState) -> APIRouter:
    router = APIRouter()

    @router.get(
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

    @router.post(
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

    @router.get("/api/ui-language", response_model=UiLanguageResponse)
    async def get_ui_language() -> UiLanguageResponse:
        """Report the interface language the launcher chose for the operator page.

        ``lt`` or ``en``; null when the server was not started by the launcher or
        the value is not one of those, and the console then keeps its own default.
        Public, even with BIBLELIVE_REQUIRE_AUTH: the display page reads it too, and
        it returns nothing but this setting.
        """
        return UiLanguageResponse(language=config.UI_LANGUAGE)

    return router
