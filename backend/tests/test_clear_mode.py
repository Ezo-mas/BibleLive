"""Clear-mode settings use the existing display-settings protocol and persistence."""

import json

import pytest

from core.server.settings_store import SettingsStore


@pytest.mark.parametrize("mode", ["blank", "idle"])
def test_clear_mode_confirms_to_all_operators_and_displays(client, mode):
    with (
        client.websocket_connect("/ws/display") as display,
        client.websocket_connect("/ws/operator") as first,
        client.websocket_connect("/ws/operator") as second,
    ):
        assert display.receive_json() == {"type": "clear", "data": {}}
        assert display.receive_json()["data"]["clear_mode"] == "idle"
        for operator in (first, second):
            assert operator.receive_json()["data"]["display_settings"]["clear_mode"] == "idle"

        first.send_json({"action": "set_clear_mode", "clear_mode": mode})
        for operator in (first, second):
            confirmed = operator.receive_json()
            assert confirmed["type"] == "display_settings_confirmed"
            assert confirmed["data"]["clear_mode"] == mode
            assert confirmed["data"]["theme"] == "dark"
            assert confirmed["data"]["verse_limit"] == 3
        assert display.receive_json() == {"type": "display_settings", "data": confirmed["data"]}
        assert client.get("/api/state").json()["display_settings"]["clear_mode"] == mode

        first.send_json({"action": "clear"})
        for operator in (first, second):
            assert operator.receive_json() == {"type": "clear_confirmed", "data": {}}
        assert display.receive_json() == {"type": "clear", "data": {}}


@pytest.mark.parametrize("value", ["picture", "", None, 3, True, [], {}])
def test_invalid_clear_mode_returns_error_and_keeps_setting(client, value):
    with client.websocket_connect("/ws/operator") as operator:
        operator.receive_json()
        operator.send_json({"action": "set_clear_mode", "clear_mode": "blank"})
        assert operator.receive_json()["data"]["clear_mode"] == "blank"
        operator.send_json({"action": "set_clear_mode", "clear_mode": value})
        error = operator.receive_json()
        assert error["type"] == "error"
        assert error["data"]["action"] == "set_clear_mode"
        assert "'idle' or 'blank'" in error["data"]["message"]
        assert client.get("/api/state").json()["display_settings"]["clear_mode"] == "blank"


def test_missing_clear_mode_returns_error(client):
    with client.websocket_connect("/ws/operator") as operator:
        operator.receive_json()
        operator.send_json({"action": "set_clear_mode"})
        error = operator.receive_json()
        assert error["type"] == "error"
        assert error["data"]["action"] == "set_clear_mode"
        assert client.get("/api/state").json()["display_settings"]["clear_mode"] == "idle"


@pytest.mark.parametrize("mode", ["idle", "blank"])
def test_saved_clear_mode_round_trip_preserves_other_settings(tmp_path, mode):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    assert store.save_theme("light")
    assert store.save_clear_mode(mode)
    assert SettingsStore(path).load_clear_mode() == mode
    assert store.load_theme() == "light"


@pytest.mark.parametrize("value", ["picture", "", None, 3, True, [], {}])
def test_invalid_saved_clear_mode_defaults_to_idle(tmp_path, caplog, value):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    assert store.load_clear_mode() == "idle"
    assert not caplog.records
    path.write_text(json.dumps({"clear_mode": value}))
    assert store.load_clear_mode() == "idle"
    assert not store.save_clear_mode(value)
    if value is not None:
        assert "Ignoring invalid clear mode" in caplog.text


@pytest.mark.asyncio
async def test_clear_mode_stays_in_force_when_persistence_fails(app_state, monkeypatch):
    monkeypatch.setattr(app_state.settings_store, "save_clear_mode", lambda _: False)
    assert await app_state.set_clear_mode("blank")
    assert app_state.display_settings.clear_mode == "blank"
    assert not await app_state.set_clear_mode("picture")
    assert app_state.display_settings.clear_mode == "blank"
