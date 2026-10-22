"""Follow-along auto-show: the operator's switch, its WebSocket action, and its persistence.

The switch is server-held, so every operator console sees the same value. It is
persisted next to the auto-approve mode (same file, same atomic write) and is off
by default. What the server shows by itself when the switch is on is covered in
test_follow_along.py (``TestFollowAuto``).
"""

import asyncio
import json
import logging

import pytest

from core.config import config
from core.server.settings_store import (
    DEFAULT_FOLLOW_AUTO,
    SettingsStore,
    coerce_follow_auto,
)
from core.server.state import AppState

# NOTE: the ``client``, ``app_state`` and autouse ``reset_app_state`` fixtures come from
# tests/conftest.py, which gives every test a fresh settings file and the switch off.


def _set_follow_auto(client, enabled):
    """Set the switch over the operator WebSocket and return the confirmation."""
    with client.websocket_connect("/ws/operator") as websocket:
        assert json.loads(websocket.receive_text())["type"] == "state_snapshot"
        websocket.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": enabled}))
        confirmed = json.loads(websocket.receive_text())
    assert confirmed["type"] == "follow_auto_confirmed"
    return confirmed


class TestSetFollowAutoAction:
    """The operator action validates, confirms to every console, and reaches the snapshot."""

    def test_snapshot_and_http_state_report_the_switch_off_by_default(self, client):
        with client.websocket_connect("/ws/operator") as websocket:
            snapshot = json.loads(websocket.receive_text())
        assert snapshot["data"]["follow_auto"] is False
        assert client.get("/api/state").json()["follow_auto"] is False

    def test_confirmation_is_broadcast_to_every_operator(self, client, app_state):
        with (
            client.websocket_connect("/ws/operator") as first,
            client.websocket_connect("/ws/operator") as second,
        ):
            assert json.loads(first.receive_text())["type"] == "state_snapshot"
            assert json.loads(second.receive_text())["type"] == "state_snapshot"

            first.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": True}))

            expected = {"type": "follow_auto_confirmed", "data": {"follow_auto": True}}
            assert json.loads(first.receive_text()) == expected
            assert json.loads(second.receive_text()) == expected

        assert app_state.follow_auto is True
        assert client.get("/api/state").json()["follow_auto"] is True

    def test_a_console_that_connects_later_gets_the_switch_in_its_snapshot(self, client):
        _set_follow_auto(client, True)

        with client.websocket_connect("/ws/operator") as late:
            assert json.loads(late.receive_text())["data"]["follow_auto"] is True

    @pytest.mark.parametrize("value", ["true", "yes", 1, 0, None, ["true"]])
    def test_non_boolean_values_are_rejected_without_breaking_the_connection(
        self, client, app_state, value
    ):
        with client.websocket_connect("/ws/operator") as websocket:
            assert json.loads(websocket.receive_text())["type"] == "state_snapshot"

            websocket.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": value}))
            error = json.loads(websocket.receive_text())
            assert error["type"] == "error"
            assert error["data"]["action"] == "set_follow_auto"
            assert app_state.follow_auto is False

            # The connection stays usable and the switch is unchanged.
            websocket.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": True}))
            assert json.loads(websocket.receive_text())["data"] == {"follow_auto": True}

    def test_missing_value_is_rejected(self, client, app_state):
        with client.websocket_connect("/ws/operator") as websocket:
            assert json.loads(websocket.receive_text())["type"] == "state_snapshot"

            websocket.send_text(json.dumps({"action": "set_follow_auto"}))
            error = json.loads(websocket.receive_text())
            assert error["type"] == "error"
            assert error["data"]["action"] == "set_follow_auto"
            assert app_state.follow_auto is False

    def test_setting_it_sends_nothing_to_displays(self, client):
        with (
            client.websocket_connect("/ws/display") as display_ws,
            client.websocket_connect("/ws/operator") as operator_ws,
        ):
            assert json.loads(display_ws.receive_text())["type"] == "clear"
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"
            assert json.loads(operator_ws.receive_text())["type"] == "state_snapshot"

            operator_ws.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": True}))
            assert json.loads(operator_ws.receive_text())["type"] == "follow_auto_confirmed"

            # The display's next message is the theme change, not anything about follow-along.
            operator_ws.send_text(json.dumps({"action": "set_display_theme", "theme": "light"}))
            assert json.loads(display_ws.receive_text())["type"] == "display_settings"

    def test_the_switch_and_the_auto_approve_mode_do_not_touch_each_other(self, client, app_state):
        with client.websocket_connect("/ws/operator") as websocket:
            assert json.loads(websocket.receive_text())["type"] == "state_snapshot"
            websocket.send_text(json.dumps({"action": "set_auto_mode", "auto_mode": "all"}))
            assert json.loads(websocket.receive_text())["type"] == "auto_mode_confirmed"

            websocket.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": True}))
            assert json.loads(websocket.receive_text())["type"] == "follow_auto_confirmed"
            assert app_state.auto_mode == "all"

            websocket.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": False}))
            assert json.loads(websocket.receive_text())["type"] == "follow_auto_confirmed"
            assert app_state.auto_mode == "all"
            assert app_state.follow_auto is False


class TestWhenFollowAlongIsTurnedOff:
    """With BIBLELIVE_FOLLOW_ALONG=false there is nothing to follow: the switch reads off."""

    @pytest.fixture(autouse=True)
    def follow_along_off(self, monkeypatch):
        monkeypatch.setattr(config, "FOLLOW_ALONG", False)

    def test_turning_it_on_is_refused_with_a_clear_error(self, client, app_state):
        with client.websocket_connect("/ws/operator") as websocket:
            assert json.loads(websocket.receive_text())["type"] == "state_snapshot"

            websocket.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": True}))
            error = json.loads(websocket.receive_text())

        assert error == {
            "type": "error",
            "data": {
                "message": "Follow-along is turned off on this server",
                "action": "set_follow_auto",
            },
        }
        assert app_state.follow_auto is False
        assert not app_state.settings_store.path.exists()

    @pytest.mark.parametrize("saved", [False, True])
    def test_a_refused_change_leaves_the_setting_and_the_saved_file_alone(
        self, client, app_state, saved
    ):
        asyncio.run(app_state.set_follow_auto(saved))
        before = app_state.settings_store.path.read_text(encoding="utf-8")

        with client.websocket_connect("/ws/operator") as websocket:
            assert json.loads(websocket.receive_text())["type"] == "state_snapshot"
            websocket.send_text(json.dumps({"action": "set_follow_auto", "follow_auto": True}))
            assert json.loads(websocket.receive_text())["type"] == "error"

        assert app_state.follow_auto is saved
        assert app_state.settings_store.path.read_text(encoding="utf-8") == before

    @pytest.mark.parametrize("saved", [False, True])
    def test_turning_it_off_is_always_accepted_and_saved(self, client, app_state, saved):
        asyncio.run(app_state.set_follow_auto(saved))

        assert _set_follow_auto(client, False) == {
            "type": "follow_auto_confirmed",
            "data": {"follow_auto": False},
        }
        assert app_state.follow_auto is False
        assert app_state.settings_store.load_follow_auto() is False

    def test_snapshot_and_http_state_report_off_while_the_saved_choice_is_on(
        self, client, app_state
    ):
        asyncio.run(app_state.set_follow_auto(True))

        with client.websocket_connect("/ws/operator") as websocket:
            snapshot = json.loads(websocket.receive_text())
        assert snapshot["data"]["follow_auto"] is False
        assert client.get("/api/state").json()["follow_auto"] is False
        # The operator's choice is kept as it was, for when follow-along is back on.
        assert app_state.follow_auto is True
        assert app_state.settings_store.load_follow_auto() is True

    def test_the_saved_choice_applies_again_once_follow_along_is_back_on(
        self, client, app_state, monkeypatch
    ):
        asyncio.run(app_state.set_follow_auto(True))
        monkeypatch.setattr(config, "FOLLOW_ALONG", True)

        with client.websocket_connect("/ws/operator") as websocket:
            assert json.loads(websocket.receive_text())["data"]["follow_auto"] is True
        assert client.get("/api/state").json()["follow_auto"] is True

    def test_turning_it_on_is_accepted_while_follow_along_is_on(
        self, client, app_state, monkeypatch
    ):
        monkeypatch.setattr(config, "FOLLOW_ALONG", True)

        assert _set_follow_auto(client, True) == {
            "type": "follow_auto_confirmed",
            "data": {"follow_auto": True},
        }
        assert app_state.follow_auto is True
        assert app_state.settings_store.load_follow_auto() is True


class TestPersistence:
    """The switch is saved like the auto-approve mode: it survives a restart."""

    def test_persisted_switch_survives_appstate_recreation(self, client, app_state):
        _set_follow_auto(client, True)

        recreated = AppState(app_state.bible_lookup, SettingsStore(app_state.settings_store.path))

        assert recreated.follow_auto is True

    def test_off_when_nothing_is_persisted(self, app_state, tmp_path):
        recreated = AppState(app_state.bible_lookup, SettingsStore(tmp_path / "fresh.json"))

        assert recreated.follow_auto is DEFAULT_FOLLOW_AUTO
        assert recreated.follow_auto is False

    def test_saved_in_the_same_file_as_the_auto_approve_mode(self, client, app_state):
        app_state.settings_store.save_auto_mode("high")
        _set_follow_auto(client, True)

        saved = json.loads(app_state.settings_store.path.read_text(encoding="utf-8"))

        assert saved == {"auto_mode": "high", "follow_auto": True}

    def test_non_boolean_in_the_settings_file_stays_off(self, app_state, tmp_path, caplog):
        path = tmp_path / "server_settings.json"
        path.write_text(json.dumps({"follow_auto": "yes"}), encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            recreated = AppState(app_state.bible_lookup, SettingsStore(path))

        assert recreated.follow_auto is False
        assert "invalid follow-along auto-show value" in caplog.text.lower()

    def test_write_failure_keeps_the_switch_in_memory(self, app_state, tmp_path, caplog):
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, not a directory", encoding="utf-8")
        state = AppState(app_state.bible_lookup, SettingsStore(blocker / "server_settings.json"))

        with caplog.at_level(logging.WARNING):
            assert asyncio.run(state.set_follow_auto(True)) is True

        assert state.follow_auto is True
        assert "could not persist settings" in caplog.text.lower()

    def test_non_boolean_is_refused_in_memory_too(self, app_state):
        assert asyncio.run(app_state.set_follow_auto("yes")) is False
        assert app_state.follow_auto is False


class TestSettingsStoreFollowAuto:
    """The store helpers: boolean-only reads, and writes that keep the other keys."""

    def test_save_keeps_other_keys(self, tmp_path):
        path = tmp_path / "server_settings.json"
        path.write_text(json.dumps({"other_setting": 7, "auto_mode": "all"}), encoding="utf-8")

        store = SettingsStore(path)
        assert store.save_follow_auto(True) is True

        assert json.loads(path.read_text(encoding="utf-8")) == {
            "other_setting": 7,
            "auto_mode": "all",
            "follow_auto": True,
        }
        assert store.load_follow_auto() is True
        assert store.load_auto_mode() == "all"

    def test_missing_file_loads_the_default(self, tmp_path):
        assert SettingsStore(tmp_path / "absent.json").load_follow_auto() is False
        assert SettingsStore(tmp_path / "absent.json").load_follow_auto(default=True) is True

    def test_coerce_accepts_only_booleans(self):
        assert coerce_follow_auto(True) is True
        assert coerce_follow_auto(False) is False
        assert coerce_follow_auto("true") is False
        assert coerce_follow_auto(1) is False
        assert coerce_follow_auto(None) is False
        assert coerce_follow_auto("true", default=True) is True
