"""Runtime switching of the Bible translation (``/api/bible-version``)."""

import asyncio
import json
import os
import shutil
import sqlite3
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from core import bible_versions
from core.bible_lookup import BibleLookup
from core.bible_versions import discover_versions
from core.config import config
from core.server import language_control
from core.server.app import create_app
from core.server.models import BibleReference
from core.server.state import AppState


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


@pytest.mark.parametrize("change", ["touch", "add", "remove", "size"])
def test_discovery_cache_invalidates_on_file_changes(bible_dir: Path, monkeypatch, change):
    read = Mock(wraps=bible_versions._read_translation)
    monkeypatch.setattr(bible_versions, "_read_translation", read)
    first = discover_versions(bible_dir)
    assert read.call_count == 3
    first.clear()
    assert sorted(discover_versions(bible_dir)) == ["kjv", "ltb"]
    assert read.call_count == 3

    path = bible_dir / "bible_ltb.sqlite"
    stat = path.stat()
    if change == "touch":
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    elif change == "add":
        _make_bible(bible_dir / "bible_web.sqlite", "web", "x")
    elif change == "remove":
        path.unlink()
    else:
        with path.open("ab") as file:
            file.write(b"\0")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    expected = ["kjv"] if change == "remove" else ["kjv", "ltb"]
    if change == "add":
        expected.append("web")
    assert sorted(discover_versions(bible_dir)) == expected
    scans = {"touch": 3, "add": 4, "remove": 2, "size": 3}
    assert read.call_count == 3 + scans[change]
    discover_versions(bible_dir)
    assert read.call_count == 3 + scans[change]


def test_discovery_cache_is_per_directory_and_warns_only_on_scan(bible_dir, tmp_path, caplog):
    _make_bible(bible_dir / "duplicate.sqlite", "kjv", "x")
    first = discover_versions(bible_dir)
    assert first["kjv"].path.name == "biblelive.sqlite"
    assert caplog.text.count("translation 'kjv' is already provided") == 1
    assert discover_versions(bible_dir) == first
    assert caplog.text.count("translation 'kjv' is already provided") == 1
    _make_bible(tmp_path / "other.sqlite", "web", "x")
    assert sorted(discover_versions(tmp_path)) == ["web"]


@pytest.mark.parametrize("phase", ["connect", "query"])
def test_discovery_retries_sqlite_errors_without_a_file_change(bible_dir, monkeypatch, phase):
    path = bible_dir / "bible_ltb.sqlite"
    before = path.stat()
    connect = sqlite3.connect
    read = Mock(wraps=bible_versions._read_translation)
    locked = Mock()
    locked.execute.side_effect = sqlite3.OperationalError("database is locked")
    failed = False

    def connect_with_transient_lock(database, *args, **kwargs):
        nonlocal failed
        if "bible_ltb.sqlite" in str(database) and not failed:
            failed = True
            if phase == "connect":
                raise sqlite3.OperationalError("database is locked")
            return locked
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(bible_versions.sqlite3, "connect", connect_with_transient_lock)
    monkeypatch.setattr(bible_versions, "_read_translation", read)
    assert sorted(discover_versions(bible_dir)) == ["kjv"]
    assert read.call_count == 3
    if phase == "query":
        locked.close.assert_called_once_with()
    assert sorted(discover_versions(bible_dir)) == ["kjv", "ltb"]
    assert read.call_count == 6
    assert sorted(discover_versions(bible_dir)) == ["kjv", "ltb"]
    assert read.call_count == 6  # The unlabelled, non-Bible file is cacheable.
    after = path.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)


@pytest.fixture
def version_state(bible_dir):
    state = AppState(BibleLookup(str(bible_dir / "biblelive.sqlite"), readonly=True))
    try:
        yield state
    finally:
        state.bible_lookup.close()
        state.service_log.stop()


@pytest.mark.asyncio
async def test_runtime_open_is_off_loop_and_install_clears_queue_atomically(
    version_state, monkeypatch
):
    state = version_state
    first = BibleReference(book="John", chapter=3, verse_start=16)
    second = BibleReference(book="John", chapter=3, verse_start=16)
    state.pending_references = [first, second]
    old_lookup = state.bible_lookup
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    open_version = state._open_bible_version
    install_version = state._install_bible_version
    count = BibleLookup.verse_count
    counts = []
    probed = []

    def install_with_probe(version, lookup):
        previous = install_version(version, lookup)
        loop.call_soon(
            lambda: probed.append((state.active_bible_version(), list(state.pending_references)))
        )
        return previous

    def count_in_worker(lookup):
        counts.append(threading.get_ident())
        return count(lookup)

    def blocked_open(version):
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return open_version(version)

    monkeypatch.setattr(BibleLookup, "verse_count", count_in_worker)
    monkeypatch.setattr(state, "_open_bible_version", blocked_open)
    monkeypatch.setattr(state, "_install_bible_version", install_with_probe)
    send = state.connection_manager.send_to_operator
    seen = []

    async def approve_after_install(message):
        if message["type"] == "bible_version_confirmed":
            seen.append((state.active_bible_version(), list(state.pending_references)))
            assert await state.approve_pending(second.id) is None
        return await send(message)

    monkeypatch.setattr(state.connection_manager, "send_to_operator", approve_after_install)
    switching = asyncio.create_task(state.change_bible_version("ltb"))
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert state.bible_lookup is old_lookup
        assert state.pending_references == [first, second]
        approved = await state.approve_pending(first.id)
        assert approved.text == "For God so loved the world"
        assert state.pending_references == [second]
    finally:
        release.set()
        await switching
    await asyncio.sleep(0)
    assert seen == [("ltb", [])]
    assert probed == [("ltb", [])]
    assert counts and all(thread != loop_thread for thread in counts)
    assert not old_lookup.ping()
    assert state.bible_lookup.ping()  # Worker-created connection works on the loop.
    await state.runtime_state.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["missing", "empty"])
async def test_failed_runtime_open_keeps_lookup_and_queue(
    version_state, bible_dir, monkeypatch, version
):
    state = version_state
    if version == "empty":
        path = bible_dir / "empty.sqlite"
        _make_bible(path, "empty", "x")
        conn = sqlite3.connect(path)
        try:
            conn.execute("DELETE FROM verses")
            conn.commit()
        finally:
            conn.close()
    old_lookup = state.bible_lookup
    ref = BibleReference(book="John", chapter=3, verse_start=16)
    state.pending_references = [ref]
    close = Mock(wraps=BibleLookup.close)

    def record_close(lookup):
        close(lookup)

    monkeypatch.setattr(BibleLookup, "close", record_close)
    error = KeyError if version == "missing" else ValueError
    with pytest.raises(error) as caught:
        await state.change_bible_version(version)
    assert caught.value.args == (
        version if version == "missing" else "Bible translation 'empty' contains no verses.",
    )
    assert state.bible_lookup is old_lookup
    assert state.active_bible_version() == "kjv"
    assert state.pending_references == [ref]
    if version == "missing":
        close.assert_not_called()
    else:
        close.assert_called_once()
        rejected = close.call_args.args[0]
        assert rejected is not old_lookup and not rejected.ping()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, BaseException])
async def test_failed_runtime_install_closes_candidate(version_state, monkeypatch, error_type):
    state = version_state
    old_lookup = state.bible_lookup
    ref = BibleReference(book="John", chapter=3, verse_start=16)
    state.pending_references = [ref]
    failure = error_type("install failed")
    candidates = []

    def fail_install(version, lookup):
        candidates.append(lookup)
        raise failure

    monkeypatch.setattr(state, "_install_bible_version", fail_install)
    with pytest.raises(error_type) as caught:
        await state.change_bible_version("ltb")
    assert caught.value is failure
    assert len(candidates) == 1 and not candidates[0].ping()
    assert state.bible_lookup is old_lookup and old_lookup.ping()
    assert state.pending_references == [ref]


@pytest.mark.asyncio
@pytest.mark.parametrize("second_version", ["web", "kjv", "ltb"])
async def test_concurrent_runtime_switches_install_in_completion_order(
    version_state, bible_dir, monkeypatch, second_version
):
    state = version_state
    _make_bible(bible_dir / "web.sqlite", "web", "x")
    loop = asyncio.get_running_loop()
    started = [asyncio.Event(), asyncio.Event()]
    release = [threading.Event(), threading.Event()]
    open_version = state._open_bible_version
    opened = []
    calls = 0

    def blocked_open(version):
        nonlocal calls
        index = calls
        calls += 1
        lookup = open_version(version)
        opened.append(lookup)
        loop.call_soon_threadsafe(started[index].set)
        assert release[index].wait(5)
        return lookup

    monkeypatch.setattr(state, "_open_bible_version", blocked_open)
    emit = Mock()
    monkeypatch.setattr(state.service_log, "emit", emit)
    messages = []

    async def record_message(message):
        messages.append(message)
        if message["type"] == "bible_version_confirmed":
            assert message["data"]["version"] == state.active_bible_version()
        return []

    monkeypatch.setattr(state.connection_manager, "send_to_operator", record_message)
    old_lookup = state.bible_lookup
    first = asyncio.create_task(state.change_bible_version("ltb"))
    second = None
    try:
        await asyncio.wait_for(started[0].wait(), 5)
        second = asyncio.create_task(state.change_bible_version(second_version))
        await asyncio.wait_for(started[1].wait(), 5)
        # The second request installs first, regardless of request order.
        release[1].set()
        assert await second == second_version
        ref = BibleReference(book="John", chapter=3, verse_start=16)
        state.pending_references = [ref]
        release[0].set()
        assert await first == "ltb"
        assert state.active_bible_version() == "ltb"
        assert state.pending_references == ([ref] if second_version == "ltb" else [])
        assert state.bible_lookup is opened[0]
        assert opened[0].ping()
        assert not opened[1].ping() and not old_lookup.ping()
        confirmed = [
            m["data"]["version"] for m in messages if m["type"] == "bible_version_confirmed"
        ]
        assert confirmed == [second_version, "ltb"]
        transitions = [
            call.kwargs for call in emit.call_args_list if call.args[0] == "translation_switched"
        ]
        expected = [] if second_version == "kjv" else [{"from": "kjv", "to": second_version}]
        if second_version != "ltb":
            expected.append({"from": second_version, "to": "ltb"})
        assert transitions == expected
    finally:
        for event in release:
            event.set()
        await asyncio.gather(first, *([second] if second is not None else []))


@pytest.mark.asyncio
async def test_cancelled_runtime_open_closes_uninstalled_lookup(version_state, monkeypatch):
    state = version_state
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    closed = asyncio.Event()
    release = threading.Event()
    open_version = state._open_bible_version
    old_lookup = state.bible_lookup
    opened = []

    def blocked_open(version):
        lookup = open_version(version)
        opened.append(lookup)
        close = lookup.close

        def record_close():
            close()
            loop.call_soon_threadsafe(closed.set)

        lookup.close = record_close
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return lookup

    monkeypatch.setattr(state, "_open_bible_version", blocked_open)
    switching = asyncio.create_task(state.change_bible_version("ltb"))
    try:
        await asyncio.wait_for(started.wait(), 5)
        switching.cancel()
        with pytest.raises(asyncio.CancelledError):
            await switching
    finally:
        release.set()
    await asyncio.wait_for(closed.wait(), 5)
    assert not opened[0].ping()
    assert state.bible_lookup is old_lookup and old_lookup.ping()


def test_loop_shutdown_closes_lookup_from_unfinished_runtime_open(version_state, monkeypatch):
    state = version_state
    started = asyncio.Event()
    release = threading.Event()
    opened = []
    open_version = state._open_bible_version
    old_lookup = state.bible_lookup

    async def start_switch():
        loop = asyncio.get_running_loop()

        def blocked_open(version):
            lookup = open_version(version)
            opened.append(lookup)
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            return lookup

        monkeypatch.setattr(state, "_open_bible_version", blocked_open)
        switching = asyncio.create_task(state.change_bible_version("ltb"))
        switching.add_done_callback(lambda _task: release.set())
        await asyncio.wait_for(started.wait(), 5)
        # Returning leaves the switch pending: asyncio.run cancels it and joins
        # the executor, whose worker must close the result without a task callback.

    try:
        asyncio.run(start_switch())
    finally:
        release.set()
    assert len(opened) == 1 and not opened[0].ping()
    assert state.bible_lookup is old_lookup and old_lookup.ping()


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


def test_fresh_start_opens_the_start_languages_bible(bible_dir: Path, monkeypatch):
    # Live test 2026-10-18: a fresh install started in Lithuanian showed KJV.
    monkeypatch.setattr(config, "START_LANGUAGE", "lt")
    with TestClient(create_app()) as started:
        assert started.get("/api/bible-version").json()["version"] == "ltb"


def test_other_languages_saved_bible_gives_way_to_the_start_language(bible_dir: Path, monkeypatch):
    # The last service ended in English (KJV saved); this one starts in Lithuanian.
    with TestClient(create_app()) as first:
        first.post("/api/bible-version", json={"version": "kjv"})
    monkeypatch.setattr(config, "START_LANGUAGE", "lt")
    with TestClient(create_app()) as second:
        assert second.get("/api/bible-version").json()["version"] == "ltb"


def test_hand_picked_bible_survives_the_start_language(bible_dir: Path, monkeypatch):
    _make_bible(bible_dir / "bible_web.sqlite", "web", "For God so loved the world")
    with TestClient(create_app()) as first:
        assert first.post("/api/bible-version", json={"version": "web"}).status_code == 200
    monkeypatch.setattr(config, "START_LANGUAGE", "lt")
    with TestClient(create_app()) as second:
        assert second.get("/api/bible-version").json()["version"] == "web"


def test_start_language_without_its_bible_keeps_the_configured_one(bible_dir: Path, monkeypatch):
    (bible_dir / "bible_ltb.sqlite").unlink()
    monkeypatch.setattr(config, "START_LANGUAGE", "lt")
    with TestClient(create_app()) as started:
        assert started.get("/api/bible-version").json()["version"] == "kjv"


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
