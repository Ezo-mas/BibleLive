"""OBS layouts use the existing display settings broadcasts and persistence."""

import json

import pytest

from core.server.models import StreamOverlaySettings
from core.server.settings_store import SettingsStore

LAYOUT = {
    "height_pct": 40,
    "position": "top",
    "background": "none",
    "font": "sans",
    "align": "center",
}


def test_stream_overlay_reaches_displays_and_operators(client, app_state):
    with (
        client.websocket_connect("/ws/display") as display,
        client.websocket_connect("/ws/operator") as first,
        client.websocket_connect("/ws/operator") as second,
    ):
        assert display.receive_json()["type"] == "clear"
        assert display.receive_json()["data"]["stream_overlay"] == (
            StreamOverlaySettings().model_dump()
        )
        for operator in (first, second):
            snapshot = operator.receive_json()
            assert snapshot["type"] == "state_snapshot"
            assert snapshot["data"]["display_settings"]["stream_overlay"] == (
                StreamOverlaySettings().model_dump()
            )

        first.send_json({"action": "set_stream_overlay", "stream_overlay": LAYOUT})
        settings = display.receive_json()
        assert settings["type"] == "display_settings"
        assert settings["data"]["stream_overlay"] == LAYOUT
        for operator in (first, second):
            assert operator.receive_json() == {
                "type": "display_settings_confirmed",
                "data": settings["data"],
            }

    with client.websocket_connect("/ws/operator") as operator:
        snapshot = operator.receive_json()
        assert snapshot["data"]["display_settings"]["stream_overlay"] == LAYOUT
    with client.websocket_connect("/ws/display") as display:
        assert display.receive_json()["type"] == "clear"
        assert display.receive_json() == settings
    assert SettingsStore(app_state.settings_store.path).load_stream_overlay().model_dump() == LAYOUT


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"stream_overlay": {}},
        {"stream_overlay": None},
        *(
            {"stream_overlay": {**LAYOUT, key: value}}
            for key, value in [
                ("height_pct", 14),
                ("height_pct", 61),
                ("height_pct", "40"),
                ("height_pct", 40.0),
                ("height_pct", True),
                ("position", "left"),
                ("background", "white"),
                ("font", "mono"),
                ("align", "right"),
                ("extra", True),
            ]
        ),
        {"stream_overlay": LAYOUT, "extra": True},
    ],
)
def test_invalid_stream_overlay_action_returns_error(client, payload):
    with client.websocket_connect("/ws/operator") as operator:
        operator.receive_json()
        operator.send_json({"action": "set_stream_overlay", **payload})
        error = operator.receive_json()
        assert error["type"] == "error"
        assert error["data"]["action"] == "set_stream_overlay"
        assert client.get("/api/state").json()["display_settings"]["stream_overlay"] == (
            StreamOverlaySettings().model_dump()
        )


def test_saved_stream_overlay_round_trip_preserves_other_settings(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    assert store.save_theme("light")
    layout = StreamOverlaySettings.model_validate(LAYOUT)
    assert store.save_stream_overlay(layout)
    restored = SettingsStore(path)
    assert restored.load_stream_overlay() == layout
    assert restored.load_theme() == "light"


@pytest.mark.parametrize("value", [None, [], {**LAYOUT, "height_pct": 90}])
def test_invalid_saved_stream_overlay_uses_defaults(tmp_path, value):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"stream_overlay": value}), encoding="utf-8")
    assert SettingsStore(path).load_stream_overlay() == StreamOverlaySettings()
