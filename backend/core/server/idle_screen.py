"""The idle screen: a picture the audience display shows while nothing is on screen.

The picture is one file in the directory that holds the persisted server settings
(``data/`` by default), never under ``bible_data/``. The settings file keeps only the
picture's kind and a random version tag; the file name comes from the fixed table
below, so no client-supplied name or path can reach the disk. Writes are atomic (a
temporary file in the same directory, then ``os.replace``), as the settings file's are.

The format is decided from the leading bytes, never from the client's content type or
file name. SVG is refused: it is XML and can carry script.
"""

import contextlib
import logging
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeGuard

from fastapi import Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# How the picture is placed: 'logo' centres it, contained in a box of half the screen,
# on the themed background; 'fill' covers the whole screen and may crop the edges.
IdleScreenFit = Literal["logo", "fill"]
ALLOWED_IDLE_SCREEN_FITS: tuple[IdleScreenFit, ...] = ("logo", "fill")
DEFAULT_IDLE_SCREEN_FIT: IdleScreenFit = "logo"

# Largest picture accepted, in bytes. The body is refused as soon as it passes this, so
# a bigger upload is never held in memory whole.
MAX_IDLE_IMAGE_BYTES = 8 * 1024 * 1024

ImageKind = Literal["png", "jpeg", "webp"]

# Content type and fixed file name for each accepted format. These are the only paths
# this feature ever writes or reads.
IMAGE_FORMATS: dict[ImageKind, tuple[str, str]] = {
    "png": ("image/png", "idle_screen.png"),
    "jpeg": ("image/jpeg", "idle_screen.jpg"),
    "webp": ("image/webp", "idle_screen.webp"),
}

_VERSION_PATTERN = re.compile(r"[0-9a-f]{16}")


class IdleScreenError(Exception):
    """A refused or failed idle-screen request, with the HTTP status to answer with."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class IdleScreenFitRequest(BaseModel):
    """Request body for choosing the fit mode."""

    fit: IdleScreenFit


class IdleScreenImageInfo(BaseModel):
    """The stored picture as the operator console sees it: never its path."""

    version: str
    content_type: str


class IdleScreenState(BaseModel):
    """The idle screen's fit mode and picture, or ``image`` null for a blank screen."""

    fit: IdleScreenFit
    image: IdleScreenImageInfo | None


@dataclass(frozen=True)
class IdleImage:
    """The stored picture: its format and the version tag of the upload that set it."""

    kind: ImageKind
    version: str

    @property
    def content_type(self) -> str:
        """The content type served with the picture, from the format table."""
        return IMAGE_FORMATS[self.kind][0]

    @property
    def filename(self) -> str:
        """The fixed file name for this format."""
        return IMAGE_FORMATS[self.kind][1]

    def path_in(self, directory: Path) -> Path:
        """Where this picture lives inside ``directory``."""
        return directory / self.filename


def sniff_image_kind(data: bytes) -> ImageKind | None:
    """The accepted format the leading bytes announce, or None when there is none."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def is_image_kind(value: object) -> TypeGuard[ImageKind]:
    """Whether ``value`` names one of the accepted formats."""
    return isinstance(value, str) and value in IMAGE_FORMATS


def is_idle_screen_fit(value: object) -> TypeGuard[IdleScreenFit]:
    """Whether ``value`` is one of the fit modes."""
    return isinstance(value, str) and value in ALLOWED_IDLE_SCREEN_FITS


def is_image_version(value: object) -> TypeGuard[str]:
    """Whether ``value`` has the shape of a version tag from ``new_image_version``."""
    return isinstance(value, str) and _VERSION_PATTERN.fullmatch(value) is not None


def new_image_version() -> str:
    """A random tag for one upload. The display URL carries it, so a new picture is never shown from cache."""
    return secrets.token_hex(8)


def _too_large_message() -> str:
    return f"The picture is larger than {MAX_IDLE_IMAGE_BYTES // (1024 * 1024)} MB."


async def read_upload(request: Request) -> bytes:
    """Read the request body, refusing it as soon as it passes the size cap.

    A declared Content-Length over the cap is refused before anything is read. Without
    one (chunked upload), the body is counted as it arrives and cut off at the cap.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_IDLE_IMAGE_BYTES:
        raise IdleScreenError(413, _too_large_message())
    received = bytearray()
    async for chunk in request.stream():
        if len(received) + len(chunk) > MAX_IDLE_IMAGE_BYTES:
            raise IdleScreenError(413, _too_large_message())
        received.extend(chunk)
    return bytes(received)


def stage_image_file(directory: Path, data: bytes) -> Path:
    """Write ``data`` to a temporary file in ``directory`` and return its path.

    The picture on show is not touched: ``commit_image_file`` moves the staged file into
    place once its record is saved, and ``discard_staged_file`` drops it otherwise.
    Raises OSError when the directory cannot be written; no partial file is left behind.
    """
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".idle_screen.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        discard_staged_file(tmp_path)
        raise
    return tmp_path


def commit_image_file(staged: Path, directory: Path, kind: ImageKind) -> None:
    """Move a staged picture to the fixed file name for ``kind``, atomically.

    Raises OSError when it cannot be moved; the staged file is removed either way.
    """
    try:
        os.replace(staged, directory / IMAGE_FORMATS[kind][1])
    except OSError:
        discard_staged_file(staged)
        raise


def discard_staged_file(staged: Path) -> None:
    """Remove a staged picture that will not be used. Best effort."""
    with contextlib.suppress(OSError):
        staged.unlink()


def remove_image_files(directory: Path, keep: ImageKind | None = None) -> None:
    """Delete the stored picture files, except the one for ``keep``. Best effort."""
    for kind, (_, filename) in IMAGE_FORMATS.items():
        if kind == keep:
            continue
        try:
            (directory / filename).unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Could not remove idle-screen file {filename}: {e}")
