"""The audience QR code: POST /api/display/qr and the ``audience_qr`` display message."""

from urllib.parse import unquote

import pytest

from core import lan
from core.config import config
from core.server.audience_qr import audience_qr, audience_url

TOKEN = "test-token-qr"
DISPLAY_URL = "http://192.168.1.23:8000/display"


@pytest.fixture
def network_server(monkeypatch):
    """A server as the launcher starts it in network mode: all interfaces, token required."""
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    monkeypatch.setattr(config, "PORT", 8000)
    monkeypatch.setattr(config, "REQUIRE_AUTH", True)
    monkeypatch.setattr(config, "OPERATOR_TOKEN", TOKEN)
    monkeypatch.setattr(lan, "_route_ip", lambda: "192.168.1.23")


def _post(client, visible, token: str | None = TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/display/qr", json={"visible": visible}, headers=headers)


# --- the address -------------------------------------------------------------------------


def test_address_is_the_lan_ip_when_listening_on_all_interfaces():
    assert audience_url("0.0.0.0", 8100, lambda: "10.0.0.5") == "http://10.0.0.5:8100/display"


def test_address_is_the_bound_interface_when_listening_on_one():
    assert audience_url("192.168.5.9", 8000, lambda: "10.0.0.5") == (
        "http://192.168.5.9:8000/display"
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_no_address_on_loopback(host):
    assert audience_url(host, 8000, lambda: "10.0.0.5") is None


def test_no_address_without_a_network_address():
    assert audience_url("0.0.0.0", 8000, lambda: None) is None


def test_code_is_an_svg_data_uri_of_the_address():
    qr = audience_qr(DISPLAY_URL)
    assert qr.url == DISPLAY_URL
    assert qr.svg is not None and qr.svg.startswith("data:image/svg+xml")
    assert "<svg" in unquote(qr.svg)


# --- the endpoint --------------------------------------------------------------------------


def test_endpoint_needs_the_operator_token(client, network_server):
    assert _post(client, True, token=None).status_code == 401
    assert _post(client, True, token="wrong").status_code in (401, 403)


@pytest.mark.parametrize("body", [{}, {"visible": "yes"}, {"visible": 1}])
def test_endpoint_rejects_a_malformed_body(client, network_server, body):
    response = client.post(
        "/api/display/qr", json=body, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 422


def test_endpoint_takes_no_address_from_the_request(client, network_server):
    response = client.post(
        "/api/display/qr",
        json={"visible": True, "url": "http://evil.example/"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.json() == {"visible": True, "url": DISPLAY_URL}


def test_show_and_hide_reach_connected_displays(client, network_server, app_state):
    with client.websocket_connect("/ws/display") as display:
        assert display.receive_json()["type"] == "clear"
        assert display.receive_json()["type"] == "display_settings"

        assert _post(client, True).json() == {"visible": True, "url": DISPLAY_URL}
        shown = display.receive_json()
        assert shown["type"] == "audience_qr"
        assert shown["data"]["url"] == DISPLAY_URL
        assert shown["data"]["svg"].startswith("data:image/svg+xml")
        assert app_state.audience_qr.url == DISPLAY_URL

        assert _post(client, False).json() == {"visible": False, "url": None}
        assert display.receive_json() == {"type": "audience_qr", "data": {"url": None, "svg": None}}
        assert app_state.audience_qr.url is None


def test_a_display_that_connects_later_gets_the_shown_code(client, network_server):
    _post(client, True)
    with client.websocket_connect("/ws/display") as display:
        kinds = [display.receive_json() for _ in range(3)]
    assert [m["type"] for m in kinds] == ["clear", "display_settings", "audience_qr"]
    assert kinds[2]["data"]["url"] == DISPLAY_URL


def test_off_air_a_new_display_still_gets_the_code(client, network_server, app_state):
    # The page keeps it hidden while off air; the server only remembers it.
    _post(client, True)
    client.portal.call(app_state.set_live, False)
    with client.websocket_connect("/ws/display") as display:
        kinds = [display.receive_json()["type"] for _ in range(3)]
    assert kinds == ["display_settings", "clear", "audience_qr"]


def test_hidden_code_is_not_sent_to_new_displays(client, network_server):
    _post(client, True)
    _post(client, False)
    with client.websocket_connect("/ws/display") as display:
        assert display.receive_json()["type"] == "clear"
        assert display.receive_json()["type"] == "display_settings"
        # A broadcast now is the next message: nothing was queued after the settings.
        _post(client, False)
        assert display.receive_json() == {"type": "audience_qr", "data": {"url": None, "svg": None}}


def test_loopback_server_keeps_the_code_hidden(client, network_server, monkeypatch):
    monkeypatch.setattr(config, "HOST", "127.0.0.1")
    with client.websocket_connect("/ws/display") as display:
        display.receive_json()
        display.receive_json()
        assert _post(client, True).json() == {"visible": False, "url": None}
        assert display.receive_json() == {"type": "audience_qr", "data": {"url": None, "svg": None}}


def test_no_network_address_keeps_the_code_hidden(client, network_server, monkeypatch):
    monkeypatch.setattr(lan, "_route_ip", lambda: None)
    monkeypatch.setattr(lan, "_host_ips", lambda: ["127.0.0.1"])
    assert _post(client, True).json() == {"visible": False, "url": None}
