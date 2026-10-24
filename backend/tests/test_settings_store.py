"""SettingsStore's save and clear methods: what is written, what is kept, and what is logged.

Every save or clear goes through the same write, so each one is held to the same contract
here: the new value is stored, the other keys stay, a failed write returns False without
raising and logs no success line, and a successful write logs one INFO line naming the
change. The read side and the atomic write itself are covered in test_auto_mode.py
(TestSettingsStore).
"""

import json
import logging

import pytest

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
