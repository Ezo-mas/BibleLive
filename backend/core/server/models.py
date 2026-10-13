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
    """A Bible reference with a stable ID for tracking.

    Detected references come from the audio pipeline; manually selected passages
    are created by the server from an operator's ``display_reference`` request.
    The two are kept distinguishable: ``source`` says which it is, and a manual
    passage carries no ``confidence`` at all rather than a made-up one, so no
    later consumer can mistake it for a detection or auto-approve it.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    book: str
    chapter: int
    verse_start: int | None = None
    verse_end: int | None = None
    confidence: Literal["high", "medium", "low"] | None = Field(
        default=None,
        description="Detection confidence; null for manually selected passages",
    )
    inferred: bool = False
    text: str = ""
    verses: list[VerseText] = Field(
        default_factory=list,
        description="Individual verses with numbers, for bounded display",
    )
    source: Literal["detected", "manual"] = Field(
        default="detected",
        description="'detected' for STT detections, 'manual' for operator-selected passages",
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
    text: str = Field(
        "",
        max_length=1000,
        description=(
            "Ignored: verse text always comes from the server's Bible database. "
            "Still accepted so older senders keep working."
        ),
    )


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


# Operator-selected auto-approve modes. Confidence order is low < medium < high,
# so "medium_and_up" (the console's "Med+") covers high as well; "off" leaves
# every detection to the operator. Mirrored in frontend/src/shared/types.ts.
AutoMode = Literal["off", "high", "medium_and_up", "all"]


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
    auto_mode: AutoMode = Field(
        default="off", description="Which new detections are displayed without operator action"
    )


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str


class LanguageRequest(BaseModel):
    """Request body for switching the audio pipeline's spoken language."""

    language: str = Field(
        ...,
        min_length=1,
        max_length=16,
        description="Language code the pipeline should transcribe in, e.g. 'en' or 'lt'",
    )


class LanguageResponse(BaseModel):
    """The language the audio pipeline is transcribing in."""

    language: str


class BookInfo(BaseModel):
    """One book of the canonical canon, for the operator's passage search.

    ``abbrev`` and ``variants`` are the canon's own abbreviation and alternative
    name forms, served so the operator can type ``ge`` or ``ps`` without the
    front end keeping a name table of its own (which could drift from the
    parser's).
    """

    name: str
    chapters: int = Field(..., gt=0, description="Number of chapters in the book")
    order: int = Field(..., gt=0, description="1-based canonical position, Genesis=1")
    abbrev: str = Field(..., description="Short form from the canon, e.g. 'gn'")
    variants: list[str] = Field(
        default_factory=list, description="Alternative name forms accepted by the parser"
    )


class BooksResponse(BaseModel):
    """The canonical book list; the front end keeps no canon of its own."""

    books: list[BookInfo]


class PassageResponse(BaseModel):
    """A resolved passage, ready to stage in Up Next and then display."""

    book: str
    chapter: int
    verse_start: int
    verse_end: int
    verses: list[VerseText]
    text: str


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


class OperatorSetAutoModeAction(BaseModel):
    """Action to choose which new detections are shown without operator approval."""

    action: Literal["set_auto_mode"]
    auto_mode: AutoMode


class OperatorDisplayReferenceAction(BaseModel):
    """Action to display a passage the operator selected by hand.

    Deliberately carries coordinates only: no reference ID (there is no pending
    reference), no confidence, and no text. The server re-resolves the passage
    against its own canon and Bible database, so a client cannot inject verse
    text or make a manual selection look like a detection.
    """

    action: Literal["display_reference"]
    book: str = Field(..., min_length=1, max_length=100, description="Canonical book name")
    chapter: int = Field(..., gt=0, le=200, description="Chapter number")
    verse_start: int | None = Field(None, gt=0, description="First verse")
    verse_end: int | None = Field(None, gt=0, description="Last verse (inclusive)")


class OperatorAuthenticateAction(BaseModel):
    """The operator token, sent as the first message on ``/ws/operator``.

    A browser cannot set headers on a WebSocket, and a token in the URL ends up
    in server and proxy logs, so it travels in the socket instead. When auth is
    on, nothing else is accepted until this arrives; when it is off, or once the
    socket is authenticated, it is ignored.
    """

    action: Literal["authenticate"]
    token: str = Field(..., max_length=512)


# Discriminated union of every operator WebSocket action, used by the server to
# validate incoming messages against the models above.
OperatorAction = Annotated[
    OperatorAuthenticateAction
    | OperatorApproveAction
    | OperatorClearDisplayAction
    | OperatorClearPendingAction
    | OperatorSetVerseLimitAction
    | OperatorSetDisplayThemeAction
    | OperatorSetAutoModeAction
    | OperatorDisplayReferenceAction,
    Field(discriminator="action"),
]
