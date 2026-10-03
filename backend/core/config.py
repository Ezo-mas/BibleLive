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
from typing import Optional

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
    DB_READONLY: bool = False

    # Bible data
    KJV_JSON_PATH: str = "data/kjv2.json"

    # Authentication configuration
    # Simple token-based auth for operator access.
    # Set BIBLELIVE_OPERATOR_TOKEN in production to protect operator endpoints.
    OPERATOR_TOKEN: Optional[str] = None
    # If True, operator endpoints require authentication
    REQUIRE_AUTH: bool = False

    # CORS configuration
    # Comma-separated list of allowed origins, or "*" for all (dev only)
    CORS_ORIGINS: str = ""

    # API configuration
    API_TITLE: str = "BibleLive API"
    API_VERSION: str = "0.1.0"
    API_DESCRIPTION: str = "Real-time Bible reference display system"

    # Queue limits to prevent unbounded memory growth
    MAX_PENDING_REFERENCES: int = 100
    MAX_HISTORY_REFERENCES: int = 200

    # Upper bound on verses fetched for one reference. Keeps a spoken range like
    # "Psalm 119" from sending a whole chapter to the display; the front end shows
    # at most 3 by default anyway.
    MAX_VERSES_PER_REFERENCE: int = 50

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

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

    def validate(self) -> None:
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
  Authentication: {auth_status} (token: {token_set})
  CORS Origins: {self.CORS_ORIGINS or "None (same-origin only)"}
  Queue Limits: pending={self.MAX_PENDING_REFERENCES}, history={self.MAX_HISTORY_REFERENCES}
"""


config = Config()
