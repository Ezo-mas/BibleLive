"""Runtime switching of the Bible translation (``/api/bible-version``)."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.bible_versions import discover_versions
from core.config import config
from core.server import language_control
from core.server.app import create_app


def _make_bible(path: Path, translation: str | None, text: str) -> None:
    """A minimal one-verse Bible database (John 3:16) in the real schema."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, name TEXT, testament TEXT, book_order INTEGER);
        CREATE TABLE verses (id INTEGER PRIMARY KEY, book_id INTEGER, chapter INTEGER,
                             verse INTEGER, text TEXT);
        CREATE TABLE bible_metadata (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO books VALUES (1, 'John', 'New Testament', 43);
        """
    )
    conn.execute("INSERT INTO verses (book_id, chapter, verse, text) VALUES (1, 3, 16, ?)", (text,))
    if translation:
        conn.execute("INSERT INTO bible_metadata VALUES ('translation', ?)", (translation,))
    conn.commit()
    conn.close()


@pytest.fixture
def bible_dir(tmp_path: Path):
    data = tmp_path / "bible_data"
    data.mkdir()
    _make_bible(data / "biblelive.sqlite", "kjv", "For God so loved the world")
    _make_bible(data / "bible_ltb.sqlite", "ltb", "Nes Dievas taip pamilo pasauli")
    # Things that must not be offered: no metadata, a backup copy, a raw source dump.
    _make_bible(data / "unlabelled.sqlite", None, "x")
    _make_bible(data / "biblelive.sqlite.bak-1", "bak", "x")
    (data / "LTB.SQLite3").write_bytes(b"not a database")

    original = (config.DB_PATH, config.SETTINGS_PATH)
    config.DB_PATH = str(data / "biblelive.sqlite")
    config.SETTINGS_PATH = str(tmp_path / "settings.json")
    yield data
    config.DB_PATH, config.SETTINGS_PATH = original


@pytest.fixture
def client(bible_dir: Path):
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_discovery_offers_only_labelled_bible_databases(bible_dir: Path):
    assert sorted(discover_versions(bible_dir)) == ["kjv", "ltb"]


def test_get_reports_active_and_available(client: TestClient):
    body = client.get("/api/bible-version").json()
    assert body == {"version": "kjv", "available": ["kjv", "ltb"]}


def test_switch_changes_verse_text_for_manual_lookup(client: TestClient):
    before = client.get("/api/lookup", params={"book": "John", "chapter": 3, "verse_start": 16})
    assert before.json()["text"] == "For God so loved the world"

    response = client.post("/api/bible-version", json={"version": "ltb"})
    assert response.status_code == 200
    assert response.json()["version"] == "ltb"

    after = client.get("/api/lookup", params={"book": "John", "chapter": 3, "verse_start": 16})
    assert after.json()["text"] == "Nes Dievas taip pamilo pasauli"


def test_unknown_version_is_404_and_changes_nothing(client: TestClient):
    assert client.post("/api/bible-version", json={"version": "nope"}).status_code == 404
    # A path-looking value must not be able to reach a file outside the registry.
    assert client.post("/api/bible-version", json={"version": "../x"}).status_code == 404
    assert client.get("/api/bible-version").json()["version"] == "kjv"


def test_switch_is_persisted_and_restored_on_next_start(bible_dir: Path):
    with TestClient(create_app()) as first:
        assert first.post("/api/bible-version", json={"version": "ltb"}).status_code == 200

    with TestClient(create_app()) as second:
        assert second.get("/api/bible-version").json()["version"] == "ltb"


def test_missing_saved_version_falls_back_to_configured_database(bible_dir: Path):
    with TestClient(create_app()) as first:
        first.post("/api/bible-version", json={"version": "ltb"})
    (bible_dir / "bible_ltb.sqlite").unlink()

    with TestClient(create_app()) as second:
        assert second.get("/api/bible-version").json()["version"] == "kjv"


def test_empty_translation_is_refused_and_old_one_stays(client: TestClient, bible_dir: Path):
    empty = bible_dir / "bible_empty.sqlite"
    _make_bible(empty, "empty", "x")
    conn = sqlite3.connect(empty)
    conn.execute("DELETE FROM verses")
    conn.commit()
    conn.close()

    assert client.post("/api/bible-version", json={"version": "empty"}).status_code == 500
    assert client.get("/api/bible-version").json()["version"] == "kjv"


def test_switch_is_broadcast_to_operators(client: TestClient):
    with client.websocket_connect("/ws/operator") as operator:
        assert json.loads(operator.receive_text())["type"] == "state_snapshot"

        assert client.post("/api/bible-version", json={"version": "ltb"}).status_code == 200

        message = json.loads(operator.receive_text())
        assert message == {
            "type": "bible_version_confirmed",
            "data": {"version": "ltb", "available": ["kjv", "ltb"]},
        }


def _stub_pipeline(
    monkeypatch: pytest.MonkeyPatch, confirmed: str, current: str | None = None
) -> None:
    """Stub the pipeline: it is in ``current`` (unreachable if None) and confirms ``confirmed``."""

    async def fake_get_language() -> str:
        if current is None:
            raise language_control.LanguageControlError(503, "not reachable")
        return current

    async def fake_set_language(_language: str) -> str:
        return confirmed

    monkeypatch.setattr(language_control, "get_language", fake_get_language)
    monkeypatch.setattr(language_control, "set_language", fake_set_language)


def test_language_switch_selects_that_languages_default_version(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _stub_pipeline(monkeypatch, "lt")
    assert client.post("/api/language", json={"language": "lt"}).status_code == 200
    assert client.get("/api/bible-version").json()["version"] == "ltb"

    _stub_pipeline(monkeypatch, "en")
    assert client.post("/api/language", json={"language": "en"}).status_code == 200
    assert client.get("/api/bible-version").json()["version"] == "kjv"


def test_language_switch_leaves_version_alone_when_auto_is_off_or_default_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _stub_pipeline(monkeypatch, "lt")
    monkeypatch.setattr(config, "AUTO_BIBLE_VERSION", False)
    assert client.post("/api/language", json={"language": "lt"}).status_code == 200
    assert client.get("/api/bible-version").json()["version"] == "kjv"

    monkeypatch.setattr(config, "AUTO_BIBLE_VERSION", True)
    monkeypatch.setattr(config, "LANGUAGE_BIBLE_VERSIONS", {"lt": "missing"})
    assert client.post("/api/language", json={"language": "lt"}).status_code == 200
    assert client.get("/api/bible-version").json()["version"] == "kjv"


def test_resending_the_same_language_keeps_the_operators_version(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    # Pipeline already in Lithuanian; the operator has chosen KJV; another tab
    # re-sends "lt". The translation must stay as the operator left it.
    _stub_pipeline(monkeypatch, confirmed="lt", current="lt")
    assert client.get("/api/bible-version").json()["version"] == "kjv"
    assert client.post("/api/language", json={"language": "lt"}).status_code == 200
    assert client.get("/api/bible-version").json()["version"] == "kjv"

    # A real change still selects the new language's default.
    _stub_pipeline(monkeypatch, confirmed="lt", current="en")
    assert client.post("/api/language", json={"language": "lt"}).status_code == 200
    assert client.get("/api/bible-version").json()["version"] == "ltb"


def _queue_john(client: TestClient) -> None:
    response = client.post(
        "/api/reference",
        json={"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high"},
    )
    assert response.status_code in (200, 201), response.text


def test_switch_drops_pending_detections_so_none_shows_the_wrong_text(client: TestClient):
    _queue_john(client)
    app_state = client.app.state.app_state
    assert len(app_state.pending_references) == 1

    with client.websocket_connect("/ws/operator") as operator:
        assert json.loads(operator.receive_text())["type"] == "state_snapshot"
        assert client.post("/api/bible-version", json={"version": "ltb"}).status_code == 200
        types = [json.loads(operator.receive_text())["type"] for _ in range(3)]
        assert types == ["bible_version_confirmed", "pending_cleared", "pending_update"]

    assert app_state.pending_references == []


def test_switch_to_the_active_version_keeps_the_queue(client: TestClient):
    _queue_john(client)
    assert client.post("/api/bible-version", json={"version": "kjv"}).status_code == 200
    assert len(client.app.state.app_state.pending_references) == 1


def test_display_never_receives_the_version_message(client: TestClient):
    with client.websocket_connect("/ws/display") as display:
        while json.loads(display.receive_text())["type"] != "display_settings":
            pass  # greeting
        assert client.post("/api/bible-version", json={"version": "ltb"}).status_code == 200
        client.post("/api/clear")
        assert json.loads(display.receive_text())["type"] == "clear"


def test_approval_landing_mid_switch_finds_the_queue_empty(client: TestClient):
    _queue_john(client)
    app_state = client.app.state.app_state
    reference_id = app_state.pending_references[0].id
    seen: dict[str, object] = {}

    original = app_state.connection_manager.send_to_operator

    async def approve_during_broadcast(message: dict):
        # The switch awaits here; an approval arriving now must not find the
        # detection that was checked against the previous translation.
        if "approved" not in seen:
            seen["approved"] = await app_state.approve_pending(reference_id)
        return await original(message)

    app_state.connection_manager.send_to_operator = approve_during_broadcast
    try:
        assert client.post("/api/bible-version", json={"version": "ltb"}).status_code == 200
    finally:
        app_state.connection_manager.send_to_operator = original

    assert seen["approved"] is None
    assert app_state.current_display is None


def test_changed_db_path_overrides_the_saved_version(bible_dir: Path):
    with TestClient(create_app()) as first:
        assert first.post("/api/bible-version", json={"version": "ltb"}).status_code == 200

    # A deployment points BIBLELIVE_DB_PATH somewhere else: that must win.
    other = bible_dir / "other.sqlite"
    shutil.copy(bible_dir / "biblelive.sqlite", other)
    config.DB_PATH = str(other)

    with TestClient(create_app()) as second:
        assert second.get("/api/bible-version").json()["version"] == "kjv"
