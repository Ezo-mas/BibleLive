"""Pytest configuration and shared fixtures.

Tests import project modules as ``core.*`` packages; the ``pythonpath`` ini
option in ``pyproject.toml`` puts the backend root on ``sys.path``.
"""

import json
from pathlib import Path

import pytest

from core.book_names import BookNames
from core.config import config
from core.import_bible import BibleImporter
from core.server.models import DisplaySettings
from core.server.settings_store import DEFAULT_AUTO_MODE, SettingsStore

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
# The complete KJV text ships in the repo (data/kjv2.json); the imported SQLite
# database does not (bible_data/ is gitignored). Fixtures that need a real
# database build one from this source so a fresh checkout and CI both work.
KJV_SOURCE = REPO_ROOT / "data" / "kjv2.json"


@pytest.fixture
def sample_bible_data() -> list[dict]:
    """Minimal Bible JSON in the same list format as ``data/kjv.json``.

    Genesis 1 (5 verses, 1-3 with text), Genesis 2:1, John 3:16-17.
    """
    return [
        {
            "abbrev": "gn",
            "chapters": [
                [
                    "In the beginning God created the heaven and the earth.",
                    "And the earth was without form, and void.",
                    "And God said, Let there be light.",
                    "",
                    "And God called the light Day.",
                ],
                ["Thus the heavens and the earth were finished."],
            ],
        },
        {
            "abbrev": "jn",
            "chapters": [
                [],
                [],
                [""] * 15
                + [
                    "For God so loved the world, that he gave his only begotten Son.",
                    "For God sent not his Son into the world to condemn the world.",
                ],
            ],
        },
    ]


@pytest.fixture
def bible_db(tmp_path: Path, sample_bible_data: list[dict]) -> Path:
    """Import ``sample_bible_data`` into a temporary SQLite database.

    Uses ``core.import_bible.BibleImporter`` (the supported import path) and
    returns the database path. The JSON source file is also written under
    ``tmp_path`` so no fixture touches the real ``data/`` or ``bible_data/``
    directories.
    """
    json_path = tmp_path / "bible.json"
    json_path.write_text(json.dumps(sample_bible_data), encoding="utf-8")
    db_path = tmp_path / "bible.sqlite"

    importer = BibleImporter(str(db_path))
    importer.connect()
    try:
        importer.create_schema()
        importer.import_kjv_json(str(json_path))
        importer.set_metadata("sample", str(json_path))
    finally:
        importer.close()
    return db_path


@pytest.fixture(scope="session")
def kjv_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Full KJV database built from the tracked ``data/kjv2.json``.

    Built once per session (~1s) and shared by the app fixture, so tests never
    depend on a developer's pre-imported ``bible_data/biblelive.sqlite``.
    """
    if not KJV_SOURCE.is_file():
        pytest.skip(f"KJV source not found at {KJV_SOURCE}")

    db_path = tmp_path_factory.mktemp("kjv") / "kjv.sqlite"
    importer = BibleImporter(str(db_path))
    importer.connect()
    try:
        importer.create_schema()
        importer.import_kjv_json(str(KJV_SOURCE))
        importer.set_metadata("kjv", str(KJV_SOURCE))
    finally:
        importer.close()
    return db_path


@pytest.fixture(scope="session")
def app(kjv_db: Path, tmp_path_factory: pytest.TempPathFactory):
    """Build the FastAPI app once per test session, backed by the test database.

    The factory lives in ``core.server.app``; the old module-level ``app``
    global from ``core.biblelive_server`` no longer exists. ``config.DB_PATH``
    is pointed at the session database for the duration of the run and restored
    afterwards. ``config.SETTINGS_PATH`` is redirected the same way so the run
    neither reads nor writes the developer's ``data/server_settings.json``.
    """
    from core.server.app import create_app

    original_db_path = config.DB_PATH
    original_settings_path = config.SETTINGS_PATH
    config.DB_PATH = str(kjv_db)
    config.SETTINGS_PATH = str(tmp_path_factory.mktemp("settings") / "server_settings.json")
    application = create_app()
    try:
        yield application
    finally:
        application.state.app_state.bible_lookup.close()
        config.DB_PATH = original_db_path
        config.SETTINGS_PATH = original_settings_path


@pytest.fixture(scope="session")
def app_state(app):
    """Expose the app's ``AppState`` instance (for tests that reset it)."""
    return app.state.app_state


@pytest.fixture(autouse=True)
def reset_app_state(app_state, tmp_path: Path):
    """Reset the shared server state before and after every test."""
    app_state.current_display = None
    app_state.pending_references.clear()
    app_state.reference_history.clear()
    app_state.display_settings = DisplaySettings()
    app_state.connection_manager = app_state.connection_manager.__class__()
    # Persisted operator settings get a per-test file, so a mode one test saves
    # cannot leak into another (or onto disk) while tests share one app.
    app_state.settings_store = SettingsStore(tmp_path / "server_settings.json")
    app_state.auto_mode = DEFAULT_AUTO_MODE
    app_state.pipeline_report = None
    app_state.pipeline_reported_at = None
    app_state.last_follow_along = None
    app_state.book_names = BookNames()
    yield
    app_state.connection_manager = app_state.connection_manager.__class__()


@pytest.fixture
def client(app):
    """FastAPI TestClient bound to the shared app (state reset via autouse)."""
    from fastapi.testclient import TestClient

    return TestClient(app)
