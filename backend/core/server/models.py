"""Pydantic models for API and WebSocket messages."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, StrictStr


class VerseText(BaseModel):
    """A single verse with its verse number and display-ready text."""

    verse: int
    text: str


class BibleReference(BaseModel):
    """A Bible reference with a stable ID for tracking.

    Detected references come from the audio pipeline; manually selected passages
    are created by the server from an operator's ``display_reference`` request,
    and followed ones (``source="follow"``) by follow-along auto-show while the
    operator has it on. They are kept distinguishable: ``source`` says which it is,
    and a manual or followed passage carries no ``confidence`` at all rather than a
    made-up one, so no later consumer can mistake it for a detection or auto-approve it.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    book: str
    book_label: str | None = Field(
        default=None,
        description=(
            "The book's name in the spoken language, for display (e.g. 'Jono evangelija'); "
            "null means show the canonical 'book'. Never used to look anything up."
        ),
    )
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
    source: Literal["detected", "manual", "follow"] = Field(
        default="detected",
        description=(
            "'detected' for STT detections, 'manual' for operator-selected passages, "
            "'follow' for a passage the server showed by itself because follow-along "
            "auto-show is on"
        ),
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


class IdleScreenPicture(BaseModel):
    """The idle-screen picture the display shows while nothing is on screen."""

    version: str = Field(
        description="Tag of this upload; the display URL carries it as a cache-buster"
    )
    fit: Literal["logo", "fill"] = Field(
        description="'logo' centres it in half the screen; 'fill' covers the screen"
    )


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
    idle_image: IdleScreenPicture | None = Field(
        default=None,
        description="Picture shown while nothing is on screen; null means a blank screen",
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
    follow_auto: bool = Field(
        default=False,
        description="Whether the server shows the verses a speaker reads on to by itself",
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


class BibleVersionRequest(BaseModel):
    """Request body for switching the Bible translation shown on the display."""

    version: str = Field(
        ...,
        min_length=1,
        max_length=16,
        description="Translation id from GET /api/bible-version, e.g. 'kjv' or 'ltb'",
    )


class BibleVersionResponse(BaseModel):
    """The active Bible translation and the ones that can be switched to."""

    version: str | None
    available: list[str]


class UiLanguageResponse(BaseModel):
    """The operator page's interface language as the launcher chose it; null when unset."""

    language: Literal["lt", "en"] | None


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
    local_name: str | None = Field(
        default=None,
        description=(
            "What the loaded Bible translation calls the book (e.g. 'Jono evangelija'); "
            "null when it has no names of its own. Display and matching only: "
            "requests still name the book by 'name'."
        ),
    )
    local_variants: list[str] = Field(
        default_factory=list,
        description="Extra spellings the search accepts for 'local_name'",
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


# Strict types: a plain ``str | int | float`` would read a long run of digits as an int
# and let it past the 300-character limit.
PipelineParam = Annotated[StrictStr, Field(max_length=300)] | StrictInt | StrictFloat


class PipelineIssue(BaseModel):
    """One pipeline problem with a stable code, so the console can translate it.

    ``text`` is the English sentence that also appears in ``problems``; ``code``
    and ``params`` let the console write its own sentence instead.
    """

    code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_]+$",
        description="Stable problem code, e.g. 'mic_silent'",
    )
    params: dict[str, PipelineParam] = Field(
        default_factory=dict,
        max_length=8,
        description="Values the sentence needs, e.g. {'seconds': 30}",
    )
    text: str = Field(
        ..., min_length=1, max_length=300, description="The English sentence, as in 'problems'"
    )


class PipelineHeartbeat(BaseModel):
    """The audio pipeline's periodic health report (``POST /api/pipeline/heartbeat``)."""

    language: str = Field(..., min_length=1, max_length=16, description="Spoken language")
    interval_s: float = Field(..., gt=0, le=300, description="Seconds until the next report is due")
    problems: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list,
        max_length=10,
        description="One sentence per thing that has stopped working; empty when healthy",
    )
    issues: list[PipelineIssue] = Field(
        default_factory=list,
        max_length=10,
        description="The same problems as 'problems', each with a code; empty from older pipelines",
    )


class PipelineStatusResponse(BaseModel):
    """What the operator console shows about the audio pipeline."""

    status: Literal["unknown", "ok", "degraded", "offline"] = Field(
        ...,
        description=(
            "'unknown': no report since the server started; 'ok': reporting, no "
            "problems; 'degraded': reporting problems; 'offline': reports stopped"
        ),
    )
    language: str | None = None
    problems: list[str] = Field(default_factory=list)
    issues: list[PipelineIssue] = Field(default_factory=list)
    seconds_since_report: float | None = Field(
        default=None, description="Age of the latest report; null when there has been none"
    )


class TranscriptSubmitRequest(BaseModel):
    """A confident transcript from the audio pipeline, used only for follow-along matching."""

    text: str = Field(..., min_length=1, max_length=2000, description="Transcript of one utterance")
    language: str = Field(
        ..., min_length=1, max_length=16, description="Spoken language, e.g. 'en' or 'lt'"
    )


class TranscriptSubmitResponse(BaseModel):
    """Response after a transcript is received. The text is not stored or echoed back."""

    status: Literal["success"]
    follow_along_active: bool = Field(
        default=False,
        description=(
            "True while the server wants partial transcripts: a passage with a verse is on "
            "screen and the speaker may be reading it. False when follow-along is off or has "
            "no verse text for the language, when nothing with a verse is on screen, or when "
            "matching failed."
        ),
    )


class FollowAlongHint(BaseModel):
    """The verses a speaker seems to be reading just after the passage on screen.

    Sent to operators only (``follow_along``), never to displays, and kept neither in
    the state snapshot nor in history. ``display_id`` is the on-screen reference the
    hint follows on from; once the display changes or clears, the hint is stale.
    Mirrored in frontend/src/shared/types.ts.
    """

    book: str
    book_label: str | None = None
    chapter: int
    verse_start: int
    verse_end: int
    display_id: str


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


class OperatorSetFollowAutoAction(BaseModel):
    """Action to choose whether follow-along shows the next verses without operator action.

    The server applies it to every operator's console at once, so several consoles never
    show the same verse twice. ``StrictBool``: a string or a number is not a choice.
    """

    action: Literal["set_follow_auto"]
    follow_auto: StrictBool


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
    | OperatorSetFollowAutoAction
    | OperatorDisplayReferenceAction,
    Field(discriminator="action"),
]
