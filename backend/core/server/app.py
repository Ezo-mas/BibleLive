"""FastAPI app factory for BibleLive server."""

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..bible_lookup import BibleLookup
from ..config import config
from . import ws
from .log_redaction import install_token_redaction
from .routes import (
    bible_version,
    health,
    idle_screen,
    language,
    operator_http,
    pages,
    pipeline,
    scripture,
)
from .state import AppState

logger = logging.getLogger(__name__)


def create_bible_lookup(db_path: str | None = None) -> BibleLookup:
    """Create and initialize a BibleLookup instance.

    Args:
        db_path: Path to the SQLite database file. When omitted, the configured
            ``config.DB_PATH`` is used resolved against the project root, so
            the server finds the same database regardless of the process's
            current working directory. An explicitly passed relative path is
            resolved against the current working directory, as usual.

    Returns:
        BibleLookup: An initialized BibleLookup instance.
    """
    if db_path is None:
        db_path = str(config.resolve_db_path())
    try:
        lookup = BibleLookup(db_path, readonly=config.DB_READONLY)
        # BibleLookup logs the translation, path and book count when there is metadata.
        if not lookup.get_metadata():
            logger.info("Bible database ready (no metadata)")
        return lookup
    except FileNotFoundError as e:
        logger.error(f"Bible database not found: {e}")
        logger.error(
            "   Run: python -m core.import_bible --translation kjv --source data/kjv2.json"
        )
        raise
    except Exception as e:
        logger.error(f"Failed to initialize Bible database: {e}")
        raise


def create_app(bible_lookup: BibleLookup | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        bible_lookup: Optional BibleLookup instance. Created from config if not provided.

    Returns:
        FastAPI: Configured application instance.
    """
    # The operator console's WebSocket carries its token in the query string,
    # which uvicorn would otherwise write to the log on every connection.
    install_token_redaction()

    injected_lookup = bible_lookup is not None
    if bible_lookup is None:
        bible_lookup = create_bible_lookup()

    app_state = AppState(bible_lookup)
    if not injected_lookup:
        # Resume the translation the operator last chose. A saved version that is
        # gone or unreadable falls back to the configured database.
        saved_version = app_state.settings_store.load_bible_version(str(config.resolve_db_path()))
        if saved_version and saved_version != app_state.active_bible_version():
            try:
                app_state.switch_bible_version(saved_version)
            except (KeyError, ValueError, OSError, sqlite3.Error) as e:
                logger.warning(f"Could not restore Bible version {saved_version!r}: {e!r}")
        # ...unless the pipeline starts in a language whose translation that is not.
        app_state.apply_start_language_version(config.START_LANGUAGE, saved_version)
        app_state.restore_runtime_state()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await app_state.shutdown()

    app = FastAPI(title="BibleLive Display System", lifespan=lifespan)
    # Expose the state holder so tests and tooling can inspect/reset it without
    # relying on module-level globals.
    app.state.app_state = app_state

    # Configure CORS if origins are specified
    if config.CORS_ORIGINS:
        origins = [origin.strip() for origin in config.CORS_ORIGINS.split(",")]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )
        logger.info(f"CORS enabled for origins: {origins}")

    # Routes use literal paths, so router order only matters if a parameterised or catch-all HTTP route is added.
    app.include_router(pages.build_router(app_state))
    app.include_router(health.build_router(app_state))
    app.include_router(ws.build_router(app_state))
    app.include_router(pipeline.build_router(app_state))
    app.include_router(operator_http.build_router(app_state))
    app.include_router(scripture.build_router(app_state))
    app.include_router(language.build_router(app_state))
    app.include_router(bible_version.build_router(app_state))
    app.include_router(idle_screen.build_router(app_state))

    # Serve static files (CSS/JS, and the built frontend bundle under /static/frontend).
    # static/ is generated and gitignored, so it is absent on a fresh checkout (and in CI)
    # until the frontend is built. Mount only when it exists: StaticFiles raises at
    # construction on a missing directory, which would make create_app() fail outright
    # instead of leaving /display and /operator to return their 503 build instructions.
    if config.static_dir.exists():
        app.mount("/static", StaticFiles(directory=config.static_dir), name="static")
    else:
        logger.warning(
            f"WARNING: Static directory not found at {config.static_dir}; "
            f"/static will not be served. Run: cd frontend && npm install && npm run build"
        )

    return app


def main() -> None:
    """Entry point for running the server."""
    config.validate_config()
    logger.info("Starting BibleLive Display System...")
    logger.info(config.summary())
    logger.info(f"Display page:  http://{config.HOST}:{config.PORT}/display")
    logger.info(f"Operator page: http://{config.HOST}:{config.PORT}/operator")
    logger.info(f"API docs:      http://{config.HOST}:{config.PORT}/docs")
    logger.info(f"Health check: http://{config.HOST}:{config.PORT}/healthz")
    logger.info(f"Ready check:   http://{config.HOST}:{config.PORT}/readyz")
    logger.info("\nPress Ctrl+C to stop\n")

    if config.RELOAD:
        # Reload needs an import string so the reloader can re-import the app;
        # a Config built from an app object silently ignores reload.
        # Same factory `make dev` uses. Watch backend/ only: the repo root
        # holds frontend/node_modules, which must not trigger restarts. The
        # path is absolute because uvicorn quietly falls back to watching the
        # whole working directory when a relative one does not exist there.
        uvicorn.run(
            "core.server.app:create_app",
            factory=True,
            host=config.HOST,
            port=config.PORT,
            reload=True,
            reload_dirs=[str(config.backend_root)],
            log_level=config.LOG_LEVEL.lower(),
        )
        return

    uvicorn_config = uvicorn.Config(
        create_app(),
        host=config.HOST,
        port=config.PORT,
        reload=False,
        log_level=config.LOG_LEVEL.lower(),
    )
    server = uvicorn.Server(uvicorn_config)

    # uvicorn installs its own signal handlers for graceful shutdown;
    # the FastAPI "shutdown" event above closes the database connection.
    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        logger.info("Shutting down server...")


if __name__ == "__main__":
    main()
