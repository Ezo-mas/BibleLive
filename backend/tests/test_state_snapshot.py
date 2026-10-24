"""The operator's state snapshot and GET /api/state describe the same state.

The WebSocket ``state_snapshot`` message and GET /api/state both serialise one
``StateResponse``, so a console that reconnects sees exactly what an HTTP poll
sees. This state exercises the parts that could drift apart: a history longer
than the 20-entry snapshot window, a pending reference, a reference on the
display, and non-default display settings.

The ``client`` and ``app_state`` fixtures come from tests/conftest.py, which
resets the state between tests.
"""

import json

from core.server.models import DisplaySettings


def _submit(client, verse_start, confidence="high"):
    """Queue a detection-like reference and return its ID."""
    response = client.post(
        "/api/reference",
        json={
            "book": "John",
            "chapter": 3,
            "verse_start": verse_start,
            "confidence": confidence,
            "inferred": False,
            "text": "",
        },
    )
    assert response.status_code == 201
    return response.json()["reference_id"]


def test_snapshot_data_equals_api_state_body(client, app_state):
    """The snapshot's data is the GET /api/state body: same keys in order, same values."""
    for verse_start in range(1, 26):  # 25 displayed: more than the 20-entry window
        reference_id = _submit(client, verse_start)
        response = client.post("/api/approve", json={"reference_id": reference_id})
        assert response.status_code == 200
    _submit(client, 30, confidence="medium")  # left pending
    app_state.display_settings = DisplaySettings(verse_limit=5, theme="light")
    app_state.auto_mode = "medium_and_up"

    # Both reads happen while this operator socket is open, so the connection
    # counts in each reply are the same.
    with client.websocket_connect("/ws/operator") as operator_ws:
        message = json.loads(operator_ws.receive_text())
        body = client.get("/api/state").json()

    assert message["type"] == "state_snapshot"
    assert list(message["data"]) == list(body)
    assert message["data"] == body

    assert body["history_count"] == 25
    assert len(body["history"]) == 20
    assert body["pending_count"] == 1
    assert body["current_display"]["verse_start"] == 25
    assert body["display_settings"]["idle_image"] is None
