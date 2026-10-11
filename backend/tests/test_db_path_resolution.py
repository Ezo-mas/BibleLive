"""Database path resolution must not depend on the working directory.

``config.DB_PATH`` is relative (``bible_data/biblelive.sqlite``); the only
correct base for the *configured* path is the project root. These tests redirect
``PROJECT_ROOT`` into a temporary directory and change the working directory
elsewhere, so they never touch the developer's live database.
"""

import json
import re
from pathlib import Path

import pytest

from core.books import CANONICAL_BOOKS
from core.config import config
from core.import_bible import BibleImporter
from core.import_bible import main as import_main
from core.server.app import create_bible_lookup


def _minimal_canon_source(path):
    path.write_text(
        json.dumps(
            [
                {"abbrev": book.abbrev, "name": book.name, "chapters": [[f"{book.name}."]]}
                for book in CANONICAL_BOOKS
            ]
        ),
        encoding="utf-8",
    )
    return str(path)


def _build_probe_db(db_path):
    """Import a tiny complete canon and stamp recognizable metadata."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    importer = BibleImporter(str(db_path))
    importer.connect()
    try:
        importer.create_schema()
        source = _minimal_canon_source(db_path.parent / "probe.json")
        importer.import_kjv_json(source)
        importer.validate_complete()
        importer.set_metadata("probe-marker", "probe.json")
    finally:
        importer.close()
    return db_path


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    """Point PROJECT_ROOT at a fresh temp dir and DB_PATH at a relative probe DB."""
    fake_root = tmp_path / "project"
    monkeypatch.setattr("core.config.PROJECT_ROOT", fake_root)
    monkeypatch.setattr(config, "DB_PATH", "bible_data/probe.sqlite")
    return fake_root


def test_default_lookup_uses_project_root_from_foreign_cwd(isolated_project, tmp_path, monkeypatch):
    db = _build_probe_db(isolated_project / "bible_data" / "probe.sqlite")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    lookup = create_bible_lookup()
    try:
        assert lookup.db_path == db
        assert lookup.get_metadata()["translation"] == "probe-marker"
        assert lookup.book_count() == 66
    finally:
        lookup.close()


def test_explicit_absolute_path_honored_from_foreign_cwd(tmp_path, monkeypatch):
    db = _build_probe_db(tmp_path / "somewhere" / "custom.sqlite")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    lookup = create_bible_lookup(str(db))
    try:
        assert lookup.get_metadata()["translation"] == "probe-marker"
    finally:
        lookup.close()


def test_missing_database_error_names_project_path(isolated_project, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    with pytest.raises(FileNotFoundError, match=re.escape(str(isolated_project))):
        create_bible_lookup()


def test_cli_default_output_targets_project_root(isolated_project, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    source = _minimal_canon_source(tmp_path / "canon.json")

    assert import_main(["--translation", "kjv", "--source", source]) == 0
    # The fixture points DB_PATH at bible_data/probe.sqlite, so the default
    # output lands there — inside the fake project root, not the working dir.
    assert (isolated_project / "bible_data" / "probe.sqlite").is_file()
    assert not (work / "bible_data").exists()


def test_cli_explicit_relative_output_uses_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = _minimal_canon_source(tmp_path / "canon.json")

    assert import_main(["--source", source, "--output", "custom.sqlite"]) == 0
    assert (tmp_path / "custom.sqlite").is_file()
