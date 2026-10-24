"""
BibleLive Configuration

Typed configuration using pydantic-settings. Values are read from
environment variables prefixed with ``BIBLELIVE_`` and from an optional
``.env`` file at the project root (see ``.env.example``). Values are bound
when this module is imported (``config = Config()``); tests can override
fields on the ``config`` instance directly, or construct a fresh ``Config``
after setting environment variables.
"""

import logging
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _find_project_root() -> Path:
    """Locate the repository root.

    The Python package lives in ``backend/core``, so the repository root is two
    levels up. Walk upwards looking for the root marker (``pyproject.toml``)
    and fall back to the ``backend/`` directory if it is missing (e.g. when the
    package is installed standalone).
    """
    backend_root = Path(__file__).resolve().parent.parent
    for candidate in (backend_root.parent, backend_root):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return backend_root


PROJECT_ROOT: Path = _find_project_root()


class Config(BaseSettings):
    """Application configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_prefix="BIBLELIVE_",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    RELOAD: bool = False
    LOG_LEVEL: str = "INFO"

    # Database configuration
    DB_PATH: str = "bible_data/biblelive.sqlite"
    DB_READONLY: bool = True

    # Server settings that must survive a restart (currently the auto-approve
    # mode). Written atomically by the server; deliberately outside bible_data/,
    # which stays read-only.
    SETTINGS_PATH: str = "data/server_settings.json"

    # Bootstrap default for the auto-approve mode: 'off' | 'high' |
    # 'medium_and_up' | 'all'. Only used when nothing valid is persisted, and a
    # value that is not one of those modes means 'off'.
    AUTO_MODE: str = "off"

    # Interface language of the operator page: 'lt' or 'en'. The launcher sets it from
    # its own setting. Anything else (or unset) means null, and the console keeps its
    # own default.
    UI_LANGUAGE: Literal["lt", "en"] | None = None

    # Authentication configuration
    # Simple token-based auth for operator access.
    # Set BIBLELIVE_OPERATOR_TOKEN in production to protect operator endpoints.
    OPERATOR_TOKEN: str | None = None
    # If True, operator endpoints require authentication
    REQUIRE_AUTH: bool = False

    # CORS configuration
    # Comma-separated list of allowed origins, or "*" for all (dev only)
    CORS_ORIGINS: str = ""

    # Queue limits to prevent unbounded memory growth
    MAX_PENDING_REFERENCES: int = 100
    MAX_HISTORY_REFERENCES: int = 200

    # Upper bound on verses fetched for one reference. Keeps a spoken range like
    # "Psalm 119" from sending a whole chapter to the display; the front end shows
    # at most 3 by default anyway.
    MAX_VERSES_PER_REFERENCE: int = 50

    # Runtime language switching. The audio pipeline is a separate process that
    # owns the microphone and serves a small control API of its own (see
    # audio/control.py). The server forwards the operator's language choice to
    # it, so the console never talks to that port directly. The URL default is the
    # pipeline's own --control-host/--control-port default, not a port of this
    # server's: change both together, and set the token here when the pipeline
    # runs with --control-token.
    AUDIO_CONTROL_URL: str = "http://127.0.0.1:8001"
    AUDIO_CONTROL_TOKEN: str | None = None
    # Seconds to wait for the pipeline before reporting it unreachable. Short on
    # purpose: a request that hangs would hold the operator's Settings dialog.
    AUDIO_CONTROL_TIMEOUT: float = 3.0

    # When the spoken language is switched, also switch to that language's default
    # Bible translation (ids as in bible_metadata, see core/bible_versions.py). A
    # language or translation that is not available is skipped, so a partial
    # install never breaks the language switch. The operator can still pick another
    # translation afterwards; it stays until the language changes again. Env
    # override for the map is JSON, e.g. BIBLELIVE_LANGUAGE_BIBLE_VERSIONS='{"lt":"ltb"}'.
    AUTO_BIBLE_VERSION: bool = True
    LANGUAGE_BIBLE_VERSIONS: dict[str, str] = {"en": "kjv", "lt": "ltb"}
    # The language the audio pipeline starts in (the launcher sets it). At startup
    # the server opens that language's translation unless the saved choice is one
    # the operator picked by hand; None leaves startup to the saved choice alone.
    START_LANGUAGE: str | None = None

    # Follow-along: while a passage is on screen, transcripts from the pipeline are
    # matched against the verses just after it, and operators are told which verse
    # the speaker has reached. Nothing reaches a display without an operator click.
    # When off, POST /api/transcript still succeeds and does nothing.
    FOLLOW_ALONG: bool = True

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("UI_LANGUAGE", mode="before")
    @classmethod
    def _ui_language(cls, value: object) -> object:
        language = str(value or "").strip().lower()
        return language if language in ("en", "lt") else None

    @field_validator("AUDIO_CONTROL_URL")
    @classmethod
    def _audio_control_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("BIBLELIVE_AUDIO_CONTROL_URL must start with http:// or https://")
        return value

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def backend_root(self) -> Path:
        return PROJECT_ROOT / "backend"

    @property
    def static_dir(self) -> Path:
        return PROJECT_ROOT / "static"

    @property
    def frontend_dist(self) -> Path:
        return PROJECT_ROOT / "static" / "frontend"

    def resolve_db_path(self) -> Path:
        """Resolve DB_PATH, which may be relative to the project root."""
        db_path = Path(self.DB_PATH)
        return db_path if db_path.is_absolute() else PROJECT_ROOT / db_path

    def resolve_settings_path(self) -> Path:
        """Resolve SETTINGS_PATH, which may be relative to the project root."""
        settings_path = Path(self.SETTINGS_PATH)
        return settings_path if settings_path.is_absolute() else PROJECT_ROOT / settings_path

    def validate_config(self) -> None:
        """Validate configuration and log warnings for production concerns."""
        # Check if reload is enabled
        if self.RELOAD:
            logger.warning(
                "WARNING: Auto-reload is ENABLED. This should only be used in development. "
                "Set BIBLELIVE_RELOAD=false for production."
            )

        # Check authentication setup - fail fast if misconfigured
        if self.REQUIRE_AUTH and not self.OPERATOR_TOKEN:
            raise ValueError(
                "BIBLELIVE_REQUIRE_AUTH is true but BIBLELIVE_OPERATOR_TOKEN is not set. "
                "Operator endpoints will reject all requests. "
                "Set BIBLELIVE_OPERATOR_TOKEN to a secure random value."
            )

        if not self.REQUIRE_AUTH and self.HOST == "0.0.0.0":
            logger.warning(
                "WARNING: Authentication is DISABLED and server is listening on all interfaces (0.0.0.0). "
                "Anyone on your network can access operator controls. "
                "Set BIBLELIVE_REQUIRE_AUTH=true and BIBLELIVE_OPERATOR_TOKEN for production."
            )

        # Check CORS configuration
        if self.CORS_ORIGINS == "*":
            logger.warning(
                "WARNING: CORS is set to allow all origins (*). "
                "This should only be used in development. "
                "Set BIBLELIVE_CORS_ORIGINS to specific origins for production."
            )

        # Check if database exists
        if not self.resolve_db_path().exists():
            logger.warning(
                f"WARNING: Database not found at {self.DB_PATH}. "
                "The server will run without Bible validation until you import one. "
                "Run: python -m core.import_bible --translation kjv --source data/kjv2.json"
            )

        # Check if frontend is built
        if not self.frontend_dist.exists() or not (self.frontend_dist / "display.html").exists():
            logger.warning(
                f"WARNING: Frontend build not found at {self.frontend_dist}. "
                "Run: cd frontend && npm install && npm run build"
            )

    def summary(self) -> str:
        """Return a formatted summary of current configuration."""
        auth_status = "ENABLED" if self.REQUIRE_AUTH else "DISABLED"
        token_set = "SET" if self.OPERATOR_TOKEN else "NOT SET"

        return f"""
BibleLive Configuration:
  Host: {self.HOST}:{self.PORT}
  Database: {self.DB_PATH} (readonly: {self.DB_READONLY})
  Frontend: {self.frontend_dist}
  Log Level: {self.LOG_LEVEL}
  Auto-reload: {self.RELOAD}
  Settings file: {self.SETTINGS_PATH} (auto-approve default: {self.AUTO_MODE})
  Authentication: {auth_status} (token: {token_set})
  CORS Origins: {self.CORS_ORIGINS or "None (same-origin only)"}
  Queue Limits: pending={self.MAX_PENDING_REFERENCES}, history={self.MAX_HISTORY_REFERENCES}
"""


config = Config()
