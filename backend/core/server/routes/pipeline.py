"""Audio pipeline routes: reference, transcript, heartbeat, levels and status."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ...config import config
from ...passage import PassageError, validate_detection
from ..auth import verify_operator_token
from ..models import (
    BibleReference,
    ErrorResponse,
    InputLevelsMessage,
    PipelineHeartbeat,
    PipelineHeartbeatResponse,
    PipelineLevels,
    PipelineStatusResponse,
    ReferenceSubmitRequest,
    ReferenceSubmitResponse,
    TranscriptSubmitRequest,
    TranscriptSubmitResponse,
)
from ..state import AppState

# Kept on the name these routes logged under before they left app.py.
logger = logging.getLogger("core.server.app")
LEVELS_PATH = "/api/pipeline/levels"


def build_router(app_state: AppState) -> APIRouter:
    router = APIRouter()

    @router.post(
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

    @router.post(
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

    @router.post(
        "/api/pipeline/heartbeat",
        status_code=200,
        response_model=PipelineHeartbeatResponse,
        responses={
            200: {"description": "Report recorded; active Bible translation"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            422: {"model": ErrorResponse, "description": "Invalid report"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def receive_pipeline_heartbeat(report: PipelineHeartbeat) -> PipelineHeartbeatResponse:
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
        return PipelineHeartbeatResponse(bible_version=app_state.active_bible_version())

    @router.post(
        LEVELS_PATH,
        status_code=204,
        responses={
            204: {"description": "Levels forwarded to operators"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            422: {"description": "Invalid level batch"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def receive_input_levels(batch: PipelineLevels) -> None:
        """Forward transient microphone peaks to the operator consoles."""
        await app_state.connection_manager.send_to_operator(
            InputLevelsMessage(data=batch).model_dump(), best_effort=True
        )

    @router.get(
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

    return router
