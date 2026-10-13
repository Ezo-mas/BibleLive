"""Test authentication and authorization for operator endpoints."""

import json

import pytest

from core.config import config

# NOTE: the ``client`` fixture comes from tests/conftest.py (built via the
# ``app`` fixture, which calls core.server.app.create_app).


@pytest.fixture
def auth_enabled():
    """Enable authentication for the duration of a test."""
    original_require = config.REQUIRE_AUTH
    original_token = config.OPERATOR_TOKEN

    config.REQUIRE_AUTH = True
    config.OPERATOR_TOKEN = "test-token-12345"

    yield "test-token-12345"

    config.REQUIRE_AUTH = original_require
    config.OPERATOR_TOKEN = original_token


def test_websocket_operator_without_auth_disabled(client):
    """Operator WebSocket works without auth when auth is disabled."""
    assert config.REQUIRE_AUTH is False
    with client.websocket_connect("/ws/operator") as websocket:
        message = json.loads(websocket.receive_text())
        assert message["type"] == "state_snapshot"


def _closed_with(client, path: str, first_message: dict | None = None) -> int:
    """Connect, optionally send one message, and return the server's close code."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect(path) as websocket:
        if first_message is not None:
            websocket.send_text(json.dumps(first_message))
        websocket.receive_text()
    return closed.value.code


def test_websocket_operator_rejected_without_token(client, auth_enabled):
    """An operator socket whose first message is not the token is closed with 1008."""
    assert _closed_with(client, "/ws/operator", {"action": "clear"}) == 1008


def test_websocket_operator_accepted_with_token_message(client, auth_enabled):
    """The token travels in the socket, not the URL, and nothing comes before it."""
    with client.websocket_connect("/ws/operator") as websocket:
        websocket.send_text(json.dumps({"action": "authenticate", "token": auth_enabled}))
        message = json.loads(websocket.receive_text())
        assert message["type"] == "state_snapshot"


@pytest.mark.parametrize(
    "first_message",
    [
        {"action": "authenticate", "token": "wrong-token"},
        {"action": "authenticate", "token": 12345},
        {"action": "authenticate"},
        {"token": "test-token-12345"},
    ],
)
def test_websocket_operator_rejects_bad_token_message(client, auth_enabled, first_message):
    assert _closed_with(client, "/ws/operator", first_message) == 1008


def test_websocket_operator_rejects_non_json_first_message(client, auth_enabled):
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect("/ws/operator") as websocket,
    ):
        websocket.send_text("not json")
        websocket.receive_text()
    assert closed.value.code == 1008


def test_websocket_operator_closed_when_token_never_arrives(client, auth_enabled, monkeypatch):
    monkeypatch.setattr("core.server.app.OPERATOR_AUTH_TIMEOUT_S", 0.1)
    assert _closed_with(client, "/ws/operator") == 1008


def test_websocket_operator_accepted_with_valid_query_token(client, auth_enabled):
    """The deprecated ?token= form keeps working for consoles built before the change."""
    token = auth_enabled
    with client.websocket_connect(f"/ws/operator?token={token}") as websocket:
        message = json.loads(websocket.receive_text())
        assert message["type"] == "state_snapshot"


def test_websocket_operator_rejects_wrong_query_token(client, auth_enabled):
    assert _closed_with(client, "/ws/operator?token=wrong") == 1008


def test_websocket_operator_non_ascii_token_is_refused_not_an_error(client, auth_enabled):
    """compare_digest raises on non-ASCII str; that must read as a wrong token."""
    assert _closed_with(client, "/ws/operator?token=t%C3%B6ken") == 1008
    assert (
        _closed_with(client, "/ws/operator", {"action": "authenticate", "token": "tökén"}) == 1008
    )


def test_http_bearer_non_ascii_token_is_refused_not_an_error(client, auth_enabled):
    """compare_digest raises on non-ASCII str; over HTTP that must read as a 401."""
    ref = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", "inferred": False}
    # Raw UTF-8 bytes on the wire: httpx will not encode non-ASCII str headers,
    # but a real client can send the bytes, which Starlette decodes as latin-1.
    raw = "Bearer tökén".encode()
    response = client.post("/api/reference", json=ref, headers=[(b"Authorization", raw)])
    assert response.status_code == 401


def test_authenticate_message_is_ignored_when_auth_is_disabled(client):
    """The console always sends its token; a server without auth just moves on."""
    assert config.REQUIRE_AUTH is False
    with client.websocket_connect("/ws/operator") as websocket:
        assert json.loads(websocket.receive_text())["type"] == "state_snapshot"
        websocket.send_text(json.dumps({"action": "authenticate", "token": "anything"}))
        websocket.send_text(json.dumps({"action": "clear"}))
        # The next message answers the clear: no error came back for authenticate.
        assert json.loads(websocket.receive_text())["type"] == "clear_confirmed"


def test_http_approve_rejected_without_token(client, auth_enabled):
    """HTTP approve is rejected with 401 when auth is enabled and no token sent."""
    ref = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", "inferred": False}
    submit_response = client.post(
        "/api/reference", json=ref, headers={"Authorization": f"Bearer {auth_enabled}"}
    )
    reference_id = submit_response.json()["reference_id"]

    response = client.post("/api/approve", json={"reference_id": reference_id})
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_http_approve_rejected_with_invalid_token(client, auth_enabled):
    """HTTP approve is rejected with 401 for a wrong token."""
    ref = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", "inferred": False}
    reference_id = client.post(
        "/api/reference", json=ref, headers={"Authorization": f"Bearer {auth_enabled}"}
    ).json()["reference_id"]

    response = client.post(
        "/api/approve",
        json={"reference_id": reference_id},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_http_approve_accepted_with_valid_token(client, auth_enabled):
    """HTTP approve is accepted with a valid token."""
    token = auth_enabled
    ref = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", "inferred": False}
    reference_id = client.post(
        "/api/reference", json=ref, headers={"Authorization": f"Bearer {token}"}
    ).json()["reference_id"]

    response = client.post(
        "/api/approve",
        json={"reference_id": reference_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_api_state_requires_auth(client, auth_enabled):
    """GET /api/state requires auth when enabled."""
    response = client.get("/api/state")
    assert response.status_code == 401

    token = auth_enabled
    response = client.get("/api/state", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "pending" in response.json()


def test_display_websocket_always_public(client, auth_enabled):
    """Display WebSocket is always public (no auth required)."""
    with client.websocket_connect("/ws/display") as websocket:
        message = json.loads(websocket.receive_text())
        assert message["type"] == "clear"


def test_api_reference_requires_token_when_auth_enabled(client, auth_enabled, app_state):
    """POST /api/reference needs the operator token when auth is on.

    Ingestion feeds the queue an auto-approve mode can put on the projector
    unseen, so it cannot be the one write endpoint left open.
    """
    ref = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", "inferred": False}

    assert client.post("/api/reference", json=ref).status_code == 401
    wrong = {"Authorization": "Bearer wrong-token"}
    assert client.post("/api/reference", json=ref, headers=wrong).status_code == 401
    assert app_state.pending_references == []

    response = client.post(
        "/api/reference", json=ref, headers={"Authorization": f"Bearer {auth_enabled}"}
    )
    assert response.status_code == 201
    assert "reference_id" in response.json()


def test_api_reference_public_when_auth_disabled(client):
    """Without auth configured, the audio pipeline needs no token (local setup)."""
    assert config.REQUIRE_AUTH is False
    ref = {"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high", "inferred": False}
    assert client.post("/api/reference", json=ref).status_code == 201
