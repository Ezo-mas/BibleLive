"""SettingsStore's save and clear methods: what is written, what is kept, and what is logged.

Every save or clear goes through the same write, so each one is held to the same contract
here: the new value is stored, the other keys stay, a failed write returns False without
raising and logs no success line, and a successful write logs one INFO line naming the
change. The read side and the atomic write itself are covered in test_auto_mode.py
(TestSettingsStore). A damaged settings file (not JSON, or JSON that is not an object) is
copied aside before a save can replace it; those tests are at the end of this file.
"""

import json
import logging
import re
from datetime import datetime

import pytest

from core.server import settings_store
from core.server.idle_screen import IdleImage
from core.server.settings_store import SettingsStore

LOGGER_NAME = "core.server.settings_store"

# Settings already on disk: one value for each key the store knows, plus an unknown key.
SEED = {
    "other_setting": 7,
    "auto_mode": "all",
    "follow_auto": False,
    "idle_screen_fit": "fill",
    "bible_version": "kjv",
    "bible_version_db_path": "/data/kjv.sqlite",
    "idle_screen_image": {"kind": "png", "version": "0123456789abcdef"},
}

# Each save or clear method: the call, the keys it sets, the keys it removes, and the INFO
# line it logs on success (``{path}`` is the settings file).
CASES = [
    pytest.param(
        lambda store: store.save_bible_version("ltb", "/data/ltb.sqlite"),
        {"bible_version": "ltb", "bible_version_db_path": "/data/ltb.sqlite"},
        (),
        "Persisted Bible version 'ltb' to {path}",
        id="bible-version",
    ),
    pytest.param(
        lambda store: store.save_auto_mode("high"),
        {"auto_mode": "high"},
        (),
        "Persisted auto-approve mode 'high' to {path}",
        id="auto-mode",
    ),
    pytest.param(
        lambda store: store.save_follow_auto(True),
        {"follow_auto": True},
        (),
        "Persisted follow-along auto-show on to {path}",
        id="follow-auto-on",
    ),
    pytest.param(
        lambda store: store.save_follow_auto(False),
        {"follow_auto": False},
        (),
        "Persisted follow-along auto-show off to {path}",
        id="follow-auto-off",
    ),
    pytest.param(
        lambda store: store.save_idle_screen_fit("logo"),
        {"idle_screen_fit": "logo"},
        (),
        "Persisted idle-screen fit 'logo' to {path}",
        id="idle-screen-fit",
    ),
    pytest.param(
        lambda store: store.save_idle_screen_image(
            IdleImage(kind="jpeg", version="fedcba9876543210")
        ),
        {"idle_screen_image": {"kind": "jpeg", "version": "fedcba9876543210"}},
        (),
        "Persisted idle-screen picture (jpeg) to {path}",
        id="idle-screen-image",
    ),
    pytest.param(
        lambda store: store.clear_idle_screen_image(),
        {},
        ("idle_screen_image",),
        "Removed the idle-screen picture record from {path}",
        id="clear-idle-screen-image",
    ),
]


@pytest.mark.parametrize(("call", "sets", "removes", "log"), CASES)
def test_the_value_is_stored_and_the_other_keys_are_kept(tmp_path, call, sets, removes, log):
    path = tmp_path / "server_settings.json"
    path.write_text(json.dumps(SEED), encoding="utf-8")
    store = SettingsStore(path)

    assert call(store) is True

    expected = {**SEED, **sets}
    for key in removes:
        expected.pop(key)
    assert json.loads(path.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize(("call", "sets", "removes", "log"), CASES)
def test_a_failed_write_returns_false_and_logs_no_success(
    tmp_path, caplog, call, sets, removes, log
):
    # A regular file where the settings directory should be makes every write fail.
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    store = SettingsStore(blocker / "server_settings.json")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert call(store) is False

    assert "could not persist settings" in caplog.text.lower()
    assert [r for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.INFO] == []


@pytest.mark.parametrize(("call", "sets", "removes", "log"), CASES)
def test_a_successful_write_logs_one_info_line(tmp_path, caplog, call, sets, removes, log):
    store = SettingsStore(tmp_path / "server_settings.json")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert call(store) is True

    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.INFO, log.format(path=store.path))
    ]


# Settings files that exist but cannot be used: not JSON, or JSON that is not an object.
DAMAGED = [
    pytest.param(b'{"auto_mode": "high", "follow_auto": true}\n}\n', id="stray-brace"),
    pytest.param(b'{"auto_mode": "high", "follow', id="truncated"),
    pytest.param(b"\xff\xfe{ not utf-8", id="not-utf-8"),
    pytest.param(b'["high"]\n', id="array"),
    pytest.param(b"true\n", id="scalar"),
]


def _backups(directory):
    """The damaged-file copies in ``directory``, as {file name: bytes}."""
    return {
        p.name: p.read_bytes()
        for p in directory.iterdir()
        if p.name.startswith("server_settings.damaged-")
    }


@pytest.mark.parametrize("damaged", DAMAGED)
def test_a_save_copies_the_damaged_file_aside_and_writes_only_the_new_key(
    tmp_path, caplog, damaged
):
    path = tmp_path / "server_settings.json"
    path.write_bytes(damaged)
    store = SettingsStore(path)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert store.save_auto_mode("high") is True

    [name] = _backups(tmp_path)
    assert re.fullmatch(r"server_settings\.damaged-\d{8}-\d{6}\.json", name)
    assert _backups(tmp_path) == {name: damaged}
    assert json.loads(path.read_text(encoding="utf-8")) == {"auto_mode": "high"}
    assert f"Kept a copy of the damaged settings file at {tmp_path / name}" in caplog.text
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert infos == [f"Persisted auto-approve mode 'high' to {path}"]


@pytest.mark.parametrize("damaged", DAMAGED)
def test_a_read_copies_the_damaged_file_and_leaves_it_as_it_is(tmp_path, damaged):
    path = tmp_path / "server_settings.json"
    path.write_bytes(damaged)

    assert SettingsStore(path).load_auto_mode() == "off"

    assert path.read_bytes() == damaged
    assert list(_backups(tmp_path).values()) == [damaged]


def test_repeated_reads_and_saves_of_one_damaged_file_keep_one_copy(tmp_path):
    damaged = b'{"auto_mode": "all"'
    path = tmp_path / "server_settings.json"
    path.write_bytes(damaged)
    store = SettingsStore(path)

    store.load_auto_mode()
    store.load_follow_auto()
    assert store.save_auto_mode("high") is True
    assert store.save_follow_auto(True) is True
    assert list(_backups(tmp_path).values()) == [damaged]

    # The same damaged bytes restored by hand are already kept, so no second copy.
    path.write_bytes(damaged)
    assert store.save_auto_mode("all") is True
    assert list(_backups(tmp_path).values()) == [damaged]


def test_different_damaged_content_gets_its_own_copy_and_a_counter_if_the_name_is_taken(
    tmp_path, monkeypatch
):
    class FixedClock:
        @staticmethod
        def now():
            return datetime(2026, 10, 24, 9, 30, 15)

    monkeypatch.setattr(settings_store, "datetime", FixedClock)
    path = tmp_path / "server_settings.json"
    store = SettingsStore(path)

    path.write_bytes(b'{"auto_mode": "all"')
    store.load_auto_mode()
    path.write_bytes(b"[1, 2")
    store.load_auto_mode()

    assert _backups(tmp_path) == {
        "server_settings.damaged-20261024-093015.json": b'{"auto_mode": "all"',
        "server_settings.damaged-20261024-093015-2.json": b"[1, 2",
    }


def test_a_missing_or_valid_file_is_never_copied_aside(tmp_path):
    path = tmp_path / "server_settings.json"
    store = SettingsStore(path)

    assert store.load_auto_mode() == "off"
    assert store.save_auto_mode("high") is True
    assert store.save_follow_auto(True) is True
    assert store.load_auto_mode() == "high"

    assert _backups(tmp_path) == {}


def test_a_save_is_refused_when_the_damaged_file_cannot_be_copied_aside(
    tmp_path, caplog, monkeypatch
):
    damaged = b'{"auto_mode": "all", "follow_auto": true}\n}\n'
    path = tmp_path / "server_settings.json"
    path.write_bytes(damaged)
    store = SettingsStore(path)

    def disk_full(backup, data):
        raise OSError("disk full")

    monkeypatch.setattr(settings_store, "_write_backup", disk_full)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert store.save_auto_mode("high") is False
        assert store.load_auto_mode() == "off"

    assert path.read_bytes() == damaged
    assert sorted(p.name for p in tmp_path.iterdir()) == ["server_settings.json"]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == [
        f"Settings not saved to {path}: the file could not be kept as a backup "
        "(disk full), so it was left unchanged"
    ]
    assert [r for r in caplog.records if r.levelno == logging.INFO] == []


def test_an_unreadable_settings_file_is_not_overwritten(tmp_path, caplog):
    # A directory where the file should be: it exists, but nothing can be read from it
    # or copied, so a save must not replace it.
    path = tmp_path / "server_settings.json"
    path.mkdir()
    store = SettingsStore(path)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        assert store.load_auto_mode() == "off"
        assert store.save_auto_mode("high") is False

    assert path.is_dir()
    assert _backups(tmp_path) == {}
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].startswith(
        f"Settings not saved to {path}: the file could not be kept as a backup ("
    )
