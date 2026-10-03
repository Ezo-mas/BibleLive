"""Pytest configuration and shared fixtures.

Tests import project modules as ``core.*`` packages; the ``pythonpath`` ini
option in ``pyproject.toml`` puts the project root on ``sys.path``.
"""

import json
from pathlib import Path

import pytest

from core.server.models import DisplaySettings

ROOT = Path(__file__).resolve().parent.parent


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
    from core.import_bible import BibleImporter

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
def app():
    """Build the FastAPI app once per test session via the app factory.

    The factory lives in ``core.server.app``; the old module-level ``app``
    global from ``core.biblelive_server`` no longer exists.
    """
    from core.server.app import create_app

    return create_app()


@pytest.fixture(scope="session")
def app_state(app):
    """Expose the app's ``AppState`` instance (for tests that reset it)."""
    return app.state.app_state


@pytest.fixture
def test_client(app):
    """Provide a FastAPI TestClient for API testing."""
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_app_state(app_state):
    """Reset the shared server state before and after every test."""
    app_state.current_display = None
    app_state.pending_references.clear()
    app_state.reference_history.clear()
    app_state.display_settings = DisplaySettings()
    app_state.connection_manager = app_state.connection_manager.__class__()
    yield
    app_state.connection_manager = app_state.connection_manager.__class__()


@pytest.fixture
def client(app):
    """FastAPI TestClient bound to the shared app (state reset via autouse)."""
    from fastapi.testclient import TestClient

    return TestClient(app)
