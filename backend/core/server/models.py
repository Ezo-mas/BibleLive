"""Pydantic models for API and WebSocket messages."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class VerseText(BaseModel):
    """A single verse with its verse number and display-ready text."""

    verse: int
    text: str


class BibleReference(BaseModel):
    """A detected Bible reference with stable ID for tracking."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    book: str
    chapter: int
    verse_start: int | None = None
    verse_end: int | None = None
    confidence: Literal["high", "medium", "low"]
    inferred: bool = False
    text: str = ""
    verses: list[VerseText] = Field(
        default_factory=list,
        description="Individual verses with numbers, for bounded display",
    )
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ReferenceSubmitRequest(BaseModel):
    """Request body for submitting a new reference to the pending queue."""

    book: str = Field(..., min_length=1, max_length=100, description="Bible book name")
    chapter: int = Field(..., gt=0, le=200, description="Chapter number")
    verse_start: int | None = Field(None, gt=0, description="Starting verse number")
    verse_end: int | None = Field(None, gt=0, description="Ending verse number")
    confidence: Literal["high", "medium", "low"] = Field(
        ..., description="Detection confidence level"
    )
    inferred: bool = Field(False, description="Whether context was inferred")
    text: str = Field("", max_length=1000, description="Optional verse text")


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


class DisplaySettings(BaseModel):
    """Operator-controlled presentation settings for the audience display.

    The display page is presentation-only, so these live on the server and are
    pushed to every connected display over the WebSocket.
    """

    verse_limit: int | Literal["all"] = Field(
        default=3,
        description="Verses shown at once; 'all' renders the full passage",
    )
    theme: Literal["dark", "light"] = Field(
        default="dark",
        description="Projector background theme; dark avoids washing out the panel",
    )


class StateResponse(BaseModel):
    """Response containing complete application state."""

    current_display: BibleReference | None
    pending: list[BibleReference]
    history: list[BibleReference]
    pending_count: int
    history_count: int
    display_connections: int
    operator_connections: int
    display_settings: DisplaySettings = Field(default_factory=DisplaySettings)


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str


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


class OperatorSetVerseLimitAction(BaseModel):
    """Action to change how many verses the display shows at once."""

    action: Literal["set_verse_limit"]
    verse_limit: int | Literal["all"]


class OperatorSetDisplayThemeAction(BaseModel):
    """Action to change the display background between dark and light."""

    action: Literal["set_display_theme"]
    theme: Literal["dark", "light"]


# Discriminated union of every operator WebSocket action, used by the server to
# validate incoming messages against the models above.
OperatorAction = Annotated[
    OperatorApproveAction
    | OperatorClearDisplayAction
    | OperatorClearPendingAction
    | OperatorSetVerseLimitAction
    | OperatorSetDisplayThemeAction,
    Field(discriminator="action"),
]
