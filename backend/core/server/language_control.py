"""Client for the audio pipeline's runtime language control.

The pipeline owns the microphone, so switching the spoken language is its call:
it serves a small loopback API of its own (``audio/control.py``,
``GET``/``POST /language``). This module is the server's side of that
conversation, so the operator console can reach the switch through the server it
already talks to — same origin, same operator authentication — instead of a
second port that a browser cannot call and a tablet cannot reach.

It forwards two requests and nothing else. The language value is the only thing a
client supplies; the address and the token come from configuration, so no request
can point the server at another host. Failures are mapped to the statuses an
operator can act on (503 when the pipeline is not running, 502 when it answers
wrongly, 400 when it refuses the language) and never leak the token.

Stdlib ``urllib`` on a worker thread, deliberately: the project keeps its
dependency set minimal, and these are two short requests on a human's timescale.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)


class LanguageControlError(Exception):
    """The pipeline's language control could not do what was asked.

    ``status_code`` is the HTTP status the server should answer with and
    ``message`` is a sentence an operator can read.
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects, so the bearer token is only ever sent where it was aimed."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _error_message(error: urllib.error.HTTPError) -> str | None:
    """The pipeline's own ``{"error": ...}`` text, when it sent one."""
    try:
        body = json.loads(error.read().decode("utf-8", errors="replace"))
    except (ValueError, OSError):
        return None
    message = body.get("error") if isinstance(body, dict) else None
    return message if isinstance(message, str) and message.strip() else None


def _call(method: str, body: dict[str, str] | None) -> str:
    """Send one request to the pipeline and return the language it reports.

    Blocking; run it off the event loop (see ``get_language``/``set_language``).
    """
    url = f"{config.AUDIO_CONTROL_URL}/language"
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    token = (config.AUDIO_CONTROL_TOKEN or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=config.AUDIO_CONTROL_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 400:
            raise LanguageControlError(
                400, _error_message(e) or "The audio pipeline did not accept that language."
            ) from e
        if e.code == 401:
            logger.error("Audio pipeline refused the control token (HTTP 401)")
            raise LanguageControlError(
                502,
                "The audio pipeline refused the server's control token. "
                "Check BIBLELIVE_AUDIO_CONTROL_TOKEN.",
            ) from e
        logger.error("Audio pipeline control answered HTTP %s", e.code)
        raise LanguageControlError(
            502, f"The audio pipeline's control API answered with an error (HTTP {e.code})."
        ) from e
    except (urllib.error.URLError, OSError) as e:
        # Connection refused, unreachable, or timed out: the pipeline is not
        # running, or was started with --no-control, or is on another address.
        logger.warning("Audio pipeline control is not reachable at %s: %s", url, e)
        raise LanguageControlError(
            503,
            "The audio pipeline is not reachable. Check that it is running with its "
            "language control enabled.",
        ) from e
    except (ValueError, http.client.HTTPException) as e:
        logger.error("Audio pipeline control sent an unreadable reply: %s", e)
        raise LanguageControlError(
            502, "The audio pipeline sent a reply the server could not read."
        ) from e

    language = payload.get("language") if isinstance(payload, dict) else None
    if not isinstance(language, str) or not language:
        logger.error("Audio pipeline control reply had no language: %r", payload)
        raise LanguageControlError(
            502, "The audio pipeline sent a reply the server could not read."
        )
    return language


async def get_language() -> str:
    """The language the pipeline is transcribing in right now."""
    return await asyncio.to_thread(_call, "GET", None)


async def set_language(language: str) -> str:
    """Ask the pipeline to switch, and return the language it confirms.

    The pipeline validates the value itself (it owns the list of languages it has
    models and parsers for), so an unsupported one comes back as a 400 with its
    own explanation.
    """
    return await asyncio.to_thread(_call, "POST", {"language": language})
