"""Save and restore uvicorn's loggers around code that builds a ``uvicorn.Config``.

A ``uvicorn.Config`` built with the default ``log_config`` runs uvicorn's
``dictConfig``, which gives the ``uvicorn`` and ``uvicorn.access`` loggers their
own handlers and turns off ``propagate``. Left that way, every later test sees
it: pytest attaches its capture handlers directly to loggers that do not
propagate, so a later test that turns ``propagate`` back on for one of them
captures each record twice.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager

from core.server.log_redaction import UVICORN_LOGGERS

LOGGER_NAMES = ("uvicorn", *UVICORN_LOGGERS)


@contextmanager
def restored_uvicorn_logging() -> Generator[None]:
    """Put uvicorn's loggers back as they were: handlers, filters, level, propagate."""
    saved = {
        name: (logger.handlers[:], logger.filters[:], logger.level, logger.propagate)
        for name in LOGGER_NAMES
        for logger in [logging.getLogger(name)]
    }
    try:
        yield
    finally:
        for name, (handlers, filters, level, propagate) in saved.items():
            logger = logging.getLogger(name)
            logger.handlers[:] = handlers
            logger.filters[:] = filters
            logger.setLevel(level)
            logger.propagate = propagate
