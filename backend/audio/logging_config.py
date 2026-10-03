"""Logging setup for the audio pipeline.

The pipeline components all log through ``logging.getLogger(__name__)``, but a
library/module should never configure the root logger itself: an application or
CLI entry point has to do it once. Without this, every ``logger.info()`` call in
capture/VAD/STT/parser/sender is silently dropped (the root logger has no handler
and its default level is WARNING).
"""

from __future__ import annotations

import logging
import os
import sys

DEFAULT_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
LOG_LEVEL_ENV_VAR = "BIBLELIVE_AUDIO_LOG_LEVEL"
DEFAULT_LOG_LEVEL = "INFO"


def resolve_log_level(level: str | int | None = None) -> int:
    """Resolve a log level from an explicit value, then the environment, then the default.

    Accepts names ("DEBUG", "info", "WARNING") or numeric levels. An unknown
    name falls back to the default instead of crashing the pipeline.
    """
    if isinstance(level, int):
        return level

    candidate = level or os.environ.get(LOG_LEVEL_ENV_VAR) or DEFAULT_LOG_LEVEL
    resolved = logging.getLevelName(str(candidate).upper())
    if isinstance(resolved, int):
        return resolved
    if str(candidate).isdigit():
        return int(candidate)
    return logging.INFO


def configure_logging(level: str | int | None = None) -> int:
    """Configure root logging for the CLI and return the resolved level.

    Idempotent: if something already installed handlers (pytest, an embedding
    app), the existing configuration is kept instead of being duplicated.
    """
    resolved = resolve_log_level(level)
    if logging.getLogger().handlers:
        logging.getLogger().setLevel(resolved)
        return resolved
    logging.basicConfig(level=resolved, format=DEFAULT_LOG_FORMAT, stream=sys.stdout)
    return resolved
