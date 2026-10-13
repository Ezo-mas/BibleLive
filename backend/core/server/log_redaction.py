"""Keep the operator token out of the server's own logs.

Browsers cannot set headers on a WebSocket, so the operator console sends its
token as ``/ws/operator?token=...``. uvicorn logs every WebSocket handshake with
its full path and query string at INFO (``"WebSocket /ws/operator?token=..."
[accepted]``), so without this the token lands in the log of every connection,
and anyone who can read the log can operate the console.

The filter rewrites the value of any ``token`` query parameter in uvicorn's log
records before they are formatted. It changes nothing on the wire: clients still
connect exactly as before. Logs written by a reverse proxy are outside this
process; SECURITY.md covers those.
"""

import logging
import re

# uvicorn logs WebSocket handshakes on "uvicorn.error" and HTTP requests on
# "uvicorn.access", in each case on that logger itself, so a logger filter sees
# every such record.
UVICORN_LOGGERS = ("uvicorn.error", "uvicorn.access")

REDACTED = "[REDACTED]"

# ``token=`` as a query parameter: after ``?`` or ``&``, up to the next
# parameter, quote, or whitespace.
_TOKEN_PARAM = re.compile(r"([?&]token=)[^&\s\"']*", re.IGNORECASE)


def redact_tokens(text: str) -> str:
    """Replace the value of every ``token`` query parameter in ``text``."""
    return _TOKEN_PARAM.sub(rf"\g<1>{REDACTED}", text)


class TokenRedactionFilter(logging.Filter):
    """Redact ``token`` query parameters in a record's message and arguments.

    Arguments are redacted one by one rather than formatting the message here,
    because uvicorn's access formatter unpacks ``record.args`` by position.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_tokens(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_tokens(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_tokens(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def install_token_redaction() -> None:
    """Attach the filter to uvicorn's loggers (idempotent).

    Safe to call before uvicorn configures logging: its ``dictConfig`` replaces
    handlers and levels but keeps filters already on a logger.
    """
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, TokenRedactionFilter) for f in logger.filters):
            logger.addFilter(TokenRedactionFilter())
