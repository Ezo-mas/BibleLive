"""The live switch: off air, the server sends displays no passage at all."""

import json

import pytest


def _submit(client, verse_start=16):
    response = client.post(
        "/api/reference",
        json={"book": "John", "chapter": 3, "verse_start": verse_start, "confidence": "high"},
    )
    assert response.status_code == 201
    return response.json()["reference_id"]


def _connect_display(display):
    """Drain a display's connect-time messages and return them."""
    return [display.receive_json(), display.receive_json()]


def _probe(operator, display):
    """Prove a display got nothing since its last read: the next message it gets is the
    settings a theme change sends, not a passage queued earlier."""
    operator.send_json({"action": "set_display_theme", "theme": "light"})
    assert operator.receive_json()["type"] == "display_settings_confirmed"
    assert display.receive_json()["type"] == "display_settings"


def _set_live(operator, live):
    operator.send_json({"action": "set_live", "live": live})
    confirmed = operator.receive_json()
    assert confirmed["type"] == "display_settings_confirmed"
    assert confirmed["data"]["live"] is live
    return confirmed["data"]


def test_server_starts_live(client):
    with client.websocket_connect("/ws/display") as display:
        clear, settings = _connect_display(display)
        assert clear["type"] == "clear"
        assert settings["data"]["live"] is True
    assert client.get("/api/state").json()["display_settings"]["live"] is True


def test_off_air_blanks_displays_then_back_on_air_resends_the_passage(client):
    with (
        client.websocket_connect("/ws/display") as display,
        client.websocket_connect("/ws/operator") as first,
        client.websocket_connect("/ws/operator") as second,
    ):
        _connect_display(display)
        for operator in (first, second):
            assert operator.receive_json()["type"] == "state_snapshot"
        first.send_json(
            {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 16}
        )
        for operator in (first, second):
            assert operator.receive_json()["type"] == "display_confirmed"
        shown = display.receive_json()
        assert shown["type"] == "reference"

        first.send_json({"action": "set_live", "live": False})
        for operator in (first, second):
            confirmed = operator.receive_json()
            assert confirmed == {"type": "display_settings_confirmed", "data": confirmed["data"]}
            assert confirmed["data"]["live"] is False
        # Settings first (a current page goes black), then a clear for an older page.
        assert display.receive_json() == {"type": "display_settings", "data": confirmed["data"]}
        assert display.receive_json() == {"type": "clear", "data": {}}
        # Off air is not a clear: the passage is still the one on screen.
        assert client.get("/api/state").json()["current_display"]["id"] == shown["data"]["id"]

        first.send_json({"action": "set_live", "live": True})
        for operator in (first, second):
            assert operator.receive_json()["data"]["live"] is True
        # The passage first, then the settings, so the display never flashes its idle picture.
        assert display.receive_json() == shown
        settings = display.receive_json()
        assert settings["type"] == "display_settings"
        assert settings["data"]["live"] is True


@pytest.mark.parametrize("path", ["approve", "display_reference", "auto_approve"])
def test_off_air_withholds_every_way_a_passage_is_shown(client, path):
    with (
        client.websocket_connect("/ws/display") as display,
        client.websocket_connect("/ws/operator") as operator,
    ):
        _connect_display(display)
        assert operator.receive_json()["type"] == "state_snapshot"
        _set_live(operator, False)
        display.receive_json()  # settings
        display.receive_json()  # clear

        if path == "approve":
            reference_id = _submit(client)
            assert operator.receive_json()["type"] == "pending_update"
            operator.send_json({"action": "approve", "reference_id": reference_id})
            assert operator.receive_json()["type"] == "approval_confirmed"
            assert operator.receive_json()["type"] == "pending_update"
        elif path == "display_reference":
            operator.send_json(
                {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 16}
            )
            assert operator.receive_json()["type"] == "display_confirmed"
        else:
            operator.send_json({"action": "set_auto_mode", "auto_mode": "all"})
            assert operator.receive_json()["type"] == "auto_mode_confirmed"
            _submit(client)
            # The manual sequence: queued, approved, queue without it.
            types = [operator.receive_json()["type"] for _ in range(3)]
            assert types == ["pending_update", "approval_confirmed", "pending_update"]

        # The operator preview follows; the audience gets nothing.
        current = client.get("/api/state").json()["current_display"]
        assert (current["book"], current["chapter"], current["verse_start"]) == ("John", 3, 16)
        _probe(operator, display)

        _set_live(operator, True)
        resent = display.receive_json()
        assert resent["type"] == "reference"
        assert resent["data"]["id"] == current["id"]


def test_display_connecting_off_air_gets_no_passage(client):
    with client.websocket_connect("/ws/operator") as operator:
        assert operator.receive_json()["type"] == "state_snapshot"
        operator.send_json(
            {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 16}
        )
        assert operator.receive_json()["type"] == "display_confirmed"
        _set_live(operator, False)

        with client.websocket_connect("/ws/display") as display:
            # Settings first, so a page with an idle picture goes black at once.
            settings, clear = _connect_display(display)
            assert settings["type"] == "display_settings"
            assert settings["data"]["live"] is False
            assert clear == {"type": "clear", "data": {}}
            assert operator.receive_json()["type"] == "connection_status"
            _probe(operator, display)


def test_cleared_while_off_air_goes_back_on_air_empty(client):
    with (
        client.websocket_connect("/ws/display") as display,
        client.websocket_connect("/ws/operator") as operator,
    ):
        _connect_display(display)
        assert operator.receive_json()["type"] == "state_snapshot"
        operator.send_json(
            {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 16}
        )
        assert operator.receive_json()["type"] == "display_confirmed"
        assert display.receive_json()["type"] == "reference"
        _set_live(operator, False)
        display.receive_json()  # settings
        display.receive_json()  # clear
        operator.send_json({"action": "clear"})
        assert operator.receive_json()["type"] == "clear_confirmed"
        assert display.receive_json()["type"] == "clear"

        _set_live(operator, True)
        settings = display.receive_json()
        assert settings["type"] == "display_settings"
        assert settings["data"]["live"] is True


@pytest.mark.parametrize("live", [True, False])
def test_repeating_the_current_state_confirms_and_sends_displays_nothing(client, live):
    with (
        client.websocket_connect("/ws/display") as display,
        client.websocket_connect("/ws/operator") as operator,
    ):
        _connect_display(display)
        assert operator.receive_json()["type"] == "state_snapshot"
        if not live:
            _set_live(operator, False)
            display.receive_json()
            display.receive_json()
        _set_live(operator, live)
        _probe(operator, display)


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None, [], {}])
def test_invalid_live_returns_error_and_keeps_state(client, value):
    with client.websocket_connect("/ws/operator") as operator:
        operator.receive_json()
        operator.send_json({"action": "set_live", "live": value})
        error = operator.receive_json()
        assert error["type"] == "error"
        assert error["data"]["action"] == "set_live"
        assert client.get("/api/state").json()["display_settings"]["live"] is True


def test_missing_live_returns_error(client):
    with client.websocket_connect("/ws/operator") as operator:
        operator.receive_json()
        operator.send_json({"action": "set_live"})
        error = operator.receive_json()
        assert error["type"] == "error"
        assert error["data"]["action"] == "set_live"


def test_settings_changed_off_air_keep_displays_dark(client):
    with (
        client.websocket_connect("/ws/display") as display,
        client.websocket_connect("/ws/operator") as operator,
    ):
        _connect_display(display)
        assert operator.receive_json()["type"] == "state_snapshot"
        _set_live(operator, False)
        display.receive_json()
        display.receive_json()
        operator.send_json({"action": "set_verse_limit", "verse_limit": 5})
        assert operator.receive_json()["data"]["live"] is False
        assert display.receive_json()["data"]["live"] is False


@pytest.mark.parametrize("order", ["shown_then_off_air", "shown_off_air"])
def test_a_restart_does_not_restore_a_passage_from_off_air(client, app_state, order):
    with client.websocket_connect("/ws/operator") as operator:
        assert operator.receive_json()["type"] == "state_snapshot"
        show = {"action": "display_reference", "book": "John", "chapter": 3, "verse_start": 16}
        if order == "shown_then_off_air":
            operator.send_json(show)
            assert operator.receive_json()["type"] == "display_confirmed"
            assert app_state.runtime_state.snapshot.current is not None
            _set_live(operator, False)
        else:
            _set_live(operator, False)
            operator.send_json(show)
            assert operator.receive_json()["type"] == "display_confirmed"
    # What a restart would restore: nothing on screen, history kept.
    assert app_state.runtime_state.snapshot.current is None
    assert app_state.runtime_state.snapshot.history[0].verse_start == 16


def test_live_is_not_persisted(client, app_state):
    with client.websocket_connect("/ws/operator") as operator:
        operator.receive_json()
        _set_live(operator, False)
    path = app_state.settings_store.path
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    assert "live" not in saved
