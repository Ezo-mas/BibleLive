"""CLI-level tests for the importer: atomic promotion and safe failure.

A failed import must never replace an existing valid database or leave a
misleading partial output behind. These tests drive ``main()`` directly with an
argument list so they never touch the developer's live database.
"""

import hashlib
import io
import json
from pathlib import Path

import pytest

from core.books import CANONICAL_BOOKS
from core.import_bible import main as import_main


def _write_json(path, payload):
    Path(path).write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _minimal_canon_source(tmp_path, filename="canon.json"):
    """A tiny but complete 66-book source in list format (one verse per book)."""
    return _write_json(
        tmp_path / filename,
        [
            {"abbrev": book.abbrev, "name": book.name, "chapters": [[f"{book.name} 1:1."]]}
            for book in CANONICAL_BOOKS
        ],
    )


def _conflicting_source(tmp_path):
    # kjv.json-style mislabeling: Judges text tagged with Jude's abbrev.
    return _write_json(
        tmp_path / "bad.json",
        [{"abbrev": "jud", "name": "Judges", "chapters": [["Judges text here."]]}],
    )


def _import_args(source, output, extra=()):
    return ["--translation", "kjv", "--source", source, "--output", str(output), *extra]


def test_successful_import_promotes_output(tmp_path):
    db = tmp_path / "cli.sqlite"
    assert import_main(_import_args(_minimal_canon_source(tmp_path), db)) == 0
    assert db.is_file()
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_failed_import_keeps_existing_database(tmp_path):
    db = tmp_path / "existing.sqlite"
    assert import_main(_import_args(_minimal_canon_source(tmp_path), db)) == 0
    before = _sha256(db)

    # A conflicting source must fail even with --force, and the original wins.
    assert import_main(_import_args(_conflicting_source(tmp_path), db, ["--force"])) != 0
    assert _sha256(db) == before
    assert list(tmp_path.glob("*.tmp-*")) == []
    assert list(db.parent.glob(f"{db.name}.tmp-*")) == []


def test_failed_import_leaves_no_output(tmp_path):
    db = tmp_path / "never.sqlite"
    assert import_main(_import_args(_conflicting_source(tmp_path), db)) != 0
    assert not db.exists()
    assert list(tmp_path.glob("*.tmp-*")) == []


class TestConsoleEncoding:
    """A hostile (e.g. cp1252) console must not turn success into failure."""

    @staticmethod
    def _cp1252_stream():
        return io.TextIOWrapper(io.BytesIO(), encoding="cp1252")

    def test_completed_import_exits_zero_on_cp1252_console(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.stdout", self._cp1252_stream())
        monkeypatch.setattr("sys.stderr", self._cp1252_stream())
        db = tmp_path / "cp1252.sqlite"
        assert import_main(_import_args(_minimal_canon_source(tmp_path), db)) == 0
        assert db.is_file()

    def test_failed_import_still_nonzero_on_cp1252_console(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.stdout", self._cp1252_stream())
        monkeypatch.setattr("sys.stderr", self._cp1252_stream())
        db = tmp_path / "cp1252bad.sqlite"
        assert import_main(_import_args(_conflicting_source(tmp_path), db)) != 0
        assert not db.exists()


class TestInfoMode:
    def test_info_without_source(self, tmp_path, capsys):
        db = tmp_path / "info.sqlite"
        assert import_main(_import_args(_minimal_canon_source(tmp_path), db)) == 0
        assert import_main(["--output", str(db), "--info"]) == 0
        assert "Translation:" in capsys.readouterr().out

    def test_info_with_source_still_works(self, tmp_path):
        db = tmp_path / "info_src.sqlite"
        assert import_main(_import_args(_minimal_canon_source(tmp_path), db)) == 0
        assert (
            import_main(
                [
                    "--source",
                    _minimal_canon_source(tmp_path, "other.json"),
                    "--output",
                    str(db),
                    "--info",
                ]
            )
            == 0
        )

    def test_info_on_missing_database(self, tmp_path):
        db = tmp_path / "missing.sqlite"
        assert import_main(["--output", str(db), "--info"]) != 0
        assert not db.exists()

    def test_import_without_source_exits_usage_error(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            import_main(["--output", str(tmp_path / "nosrc.sqlite")])
        assert exc.value.code == 2
        assert not (tmp_path / "nosrc.sqlite").exists()

    def test_help_lists_info_without_source(self, capsys):
        with pytest.raises(SystemExit) as exc:
            import_main(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--info" in out and "--source" in out
