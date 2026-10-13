"""The operator token must not reach the server log via the WebSocket URL."""

import logging

import pytest
import uvicorn
from uvicorn.logging import AccessFormatter

from core.server.log_redaction import (
    REDACTED,
    UVICORN_LOGGERS,
    TokenRedactionFilter,
    install_token_redaction,
    redact_tokens,
)

SECRET = "s3cr3t-Operator_Token"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"/ws/operator?token={SECRET}", f"/ws/operator?token={REDACTED}"),
        (f"/ws/operator?a=1&token={SECRET}&b=2", f"/ws/operator?a=1&token={REDACTED}&b=2"),
        (
            f'"WebSocket /ws/operator?TOKEN={SECRET}" 403',
            f'"WebSocket /ws/operator?TOKEN={REDACTED}" 403',
        ),
        ("/ws/display", "/ws/display"),
        ("/api/lookup?book=John&chapter=3", "/api/lookup?book=John&chapter=3"),
        # Only a parameter named exactly "token".
        ("/x?csrftoken=keep&mytoken=keep", "/x?csrftoken=keep&mytoken=keep"),
    ],
)
def test_redact_tokens(text, expected):
    assert redact_tokens(text) == expected


class _Collect(logging.Handler):
    def __init__(self, formatter: logging.Formatter | None = None):
        super().__init__()
        self.lines: list[str] = []
        if formatter is not None:
            self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def uvicorn_logging():
    """Run uvicorn's own logging setup, then restore the loggers afterwards."""
    saved = {
        name: (logger.handlers[:], logger.filters[:], logger.level, logger.propagate)
        for name in ("uvicorn", *UVICORN_LOGGERS)
        for logger in [logging.getLogger(name)]
    }
    yield
    for name, (handlers, filters, level, propagate) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.filters[:] = filters
        logger.setLevel(level)
        logger.propagate = propagate


def test_websocket_handshake_line_is_redacted_after_uvicorn_configures_logging(uvicorn_logging):
    install_token_redaction()
    # Building a Config applies uvicorn's dictConfig, as `python -m core.server` does.
    uvicorn.Config(app=lambda scope, receive, send: None, log_level="info")
    logger = logging.getLogger("uvicorn.error")
    collect = _Collect()
    logger.addHandler(collect)

    # The exact call uvicorn's websockets implementation makes on a handshake.
    logger.info('%s - "WebSocket %s" [accepted]', "127.0.0.1:5000", f"/ws/operator?token={SECRET}")

    assert collect.lines == [
        f'127.0.0.1:5000 - "WebSocket /ws/operator?token={REDACTED}" [accepted]'
    ]


def test_access_log_line_is_redacted_and_still_formats(uvicorn_logging):
    """uvicorn's AccessFormatter unpacks record.args by position."""
    install_token_redaction()
    uvicorn.Config(app=lambda scope, receive, send: None, log_level="info")
    logger = logging.getLogger("uvicorn.access")
    collect = _Collect(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s'))
    logger.addHandler(collect)

    logger.info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", f"/x?token={SECRET}", "1.1", 200
    )

    [line] = collect.lines
    assert SECRET not in line
    assert f"GET /x?token={REDACTED} HTTP/1.1" in line
    assert line.endswith("200 OK")


def test_install_is_idempotent(uvicorn_logging):
    install_token_redaction()
    install_token_redaction()

    for name in UVICORN_LOGGERS:
        filters = logging.getLogger(name).filters
        assert sum(isinstance(f, TokenRedactionFilter) for f in filters) == 1


def test_create_app_installs_the_filter(app):  # noqa: ARG001 - building the app is the point
    for name in UVICORN_LOGGERS:
        assert any(isinstance(f, TokenRedactionFilter) for f in logging.getLogger(name).filters)


def test_dict_args_are_redacted():
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "%(path)s", None, None)
    record.args = {"path": f"/ws/operator?token={SECRET}"}

    TokenRedactionFilter().filter(record)

    assert record.getMessage() == f"/ws/operator?token={REDACTED}"
