"""Local HTTP control server for switching the pipeline language at runtime.

The pipeline owns the microphone, so telling it to switch between English and
Lithuanian needs a small side channel that is independent of the FastAPI server
and the WebSocket reference/approval protocol. This is a stdlib ``http.server``
bound to loopback by default.

Control configuration (host/port/token) lives in ``AudioPipelineConfig``, not in
the server's core config.

    GET  /language           -> {"language": "en"}
    POST /language {"language": "lt"} -> {"language": "lt"}

A malformed request gets ``400`` and an unknown path gets ``404``. When a token
is configured the caller must send ``Authorization: Bearer <token>`` (the same
convention as the operator HTTP endpoints).
"""

from __future__ import annotations

import errno
import json
import logging
import secrets
import socket
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Hosts that are only reachable from this machine; binding one of these needs no
# token for the default local setup.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class _ControlHTTPServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` carrying the language callbacks and token."""

    # Request handler threads must not keep ``stop()`` waiting.
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        get_language: Callable[[], str],
        set_language: Callable[[str], None],
        token: str | None,
    ) -> None:
        self.get_language = get_language
        self.set_language = set_language
        self.token = token
        super().__init__(server_address, _LanguageControlHandler)


class _LanguageControlHandler(BaseHTTPRequestHandler):
    """Handles ``GET``/``POST`` on ``/language``."""

    server: _ControlHTTPServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming convention
        if not self._route_matches():
            return
        if not self._authorized():
            return
        self._send_json(200, {"language": self.server.get_language()})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming convention
        if not self._route_matches():
            return
        if not self._authorized():
            return

        language = self._read_language()
        if language is None:
            return

        try:
            self.server.set_language(language)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        self._send_json(200, {"language": language})

    # ---- helpers ----------------------------------------------------------
    def _route_matches(self) -> bool:
        """Return True for ``/language``; otherwise send 404 and return False."""
        if urlsplit(self.path).path != "/language":
            self._send_json(404, {"error": "not found"})
            return False
        return True

    def _authorized(self) -> bool:
        """Return True when no token is set or the bearer token matches."""
        token = self.server.token
        if token is None:
            return True

        header = self.headers.get("Authorization", "")
        parts = header.split()
        provided = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
        if not provided or not secrets.compare_digest(provided, token):
            self._send_json(
                401, {"error": "unauthorized"}, extra_headers={"WWW-Authenticate": "Bearer"}
            )
            return False
        return True

    def _read_language(self) -> str | None:
        """Read and validate the requested language; send 400 on any problem."""
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length header"})
            return None
        if length <= 0:
            self._send_json(400, {"error": "request body required"})
            return None

        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "malformed JSON body"})
            return None

        raw_language = payload.get("language") if isinstance(payload, dict) else None
        if not isinstance(raw_language, str):
            self._send_json(400, {"error": "'language' must be a string"})
            return None

        language = raw_language.strip().lower()
        if not language:
            self._send_json(400, {"error": "'language' must not be empty"})
            return None
        return language

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Route access logs through logging instead of stderr.
        logger.debug("%s - %s", self.address_string(), format % args)


class LanguageControlServer:
    """Owns the control HTTP server lifecycle.

    ``start()`` binds the socket before the caller starts the pipeline, so a
    port conflict fails startup immediately and clearly. ``stop()`` is
    idempotent.
    """

    def __init__(
        self,
        get_language: Callable[[], str],
        set_language: Callable[[str], None],
        host: str = "127.0.0.1",
        port: int = 8001,
        token: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        # Authorization headers are whitespace-tokenized, so surrounding
        # whitespace can never be part of a presented credential. An empty (or
        # whitespace-only) token therefore means "no token" instead of a
        # credential no client could satisfy, which would 401 every request.
        self.token = (token or "").strip() or None
        self._get_language = get_language
        self._set_language = set_language
        self._httpd: _ControlHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int] | None:
        """The bound ``(host, port)`` once started (useful when ``port=0``)."""
        if self._httpd is None:
            return None
        bound_host, bound_port = self._httpd.server_address[:2]
        return str(bound_host), int(bound_port)

    def _port_has_live_listener(self) -> bool:
        """Return True when something already accepts connections on the address.

        This is a best-effort pre-bind check, not a portable guarantee. A
        successful connection is evidence of a reachable listener — the case
        that matters, since Windows lets a second socket bind an address and
        port that already has a listener when both set ``SO_REUSEADDR``
        (which ``http.server`` enables via ``allow_reuse_address``), so a
        conflicting listener does not make ``bind()`` fail there. But a
        failed connection does not prove the port is free: a timeout, packet
        filtering, a saturated listen backlog, or any other connection
        failure produces a false negative and falls through to the real
        ``bind()``, which remains the authority. Wildcard bind hosts such as
        ``0.0.0.0`` cannot be reliably probed as written on Windows, and the
        check cannot eliminate the race between the probe and the subsequent
        bind. An orderly close of the probe connection is silent on the
        server side.
        """
        if self.port == 0:
            return False  # An ephemeral port cannot conflict.
        try:
            with socket.create_connection((self.host, self.port), timeout=0.5):
                return True
        except OSError:
            return False

    def start(self) -> None:
        """Bind the socket and serve in a background thread.

        Raises:
            OSError: if the address is already in use or cannot be bound.
            RuntimeError: if the server has already been started.
        """
        if self._httpd is not None:
            raise RuntimeError("control server already started")

        if self.token is None and self.host not in LOOPBACK_HOSTS:
            logger.warning(
                "Control server bound to non-loopback host %s without a token; "
                "anyone who can reach it can switch the pipeline language",
                self.host,
            )

        if self._port_has_live_listener():
            logger.error(
                "Control server could not bind %s:%s: address already in use",
                self.host,
                self.port,
            )
            raise OSError(
                errno.EADDRINUSE,
                f"Control server port {self.host}:{self.port} already in use",
            )

        try:
            httpd = _ControlHTTPServer(
                (self.host, self.port), self._get_language, self._set_language, self.token
            )
        except OSError as e:
            logger.error("Control server could not bind %s:%s: %s", self.host, self.port, e)
            raise

        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="language-control-server", daemon=True
        )
        self._thread.start()
        bound_host, bound_port = httpd.server_address[:2]
        logger.info("Language control server listening on http://%s:%s", bound_host, bound_port)

    def stop(self) -> None:
        """Stop serving and release the socket. Safe to call more than once."""
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._httpd = None
        self._thread = None
        logger.info("Language control server stopped")
