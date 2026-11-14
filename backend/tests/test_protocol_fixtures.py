"""Golden wire examples shared with the frontend's real message handlers.

Set BIBLELIVE_UPDATE_PROTOCOL_FIXTURES=1 to regenerate checked-in examples.
The JSON text comparisons include key order, nulls and whitespace on the wire.
"""

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter

from core.bible_versions import BibleVersion
from core.config import config
from core.server import models
from core.server.app import _OPERATOR_ACTION_ADAPTER
from core.server.models import DisplayServerMessage, FollowAlongHint, OperatorServerMessage

FIXTURES = Path(__file__).parent / "fixtures" / "protocol"
REFERENCE_ID = "00000000-0000-0000-0000-000000000001"
ACTIONS = {
    "authenticate": {"action": "authenticate", "token": "example-token"},
    "approve": {"action": "approve", "reference_id": REFERENCE_ID},
    "clear": {"action": "clear"},
    "clear_pending": {"action": "clear_pending"},
    "set_verse_limit": {"action": "set_verse_limit", "verse_limit": 5},
    "set_display_theme": {"action": "set_display_theme", "theme": "light"},
    "set_auto_mode": {"action": "set_auto_mode", "auto_mode": "high"},
    "set_follow_auto": {"action": "set_follow_auto", "follow_auto": True},
    "display_reference": {
        "action": "display_reference",
        "book": "John",
        "chapter": 3,
        "verse_start": 16,
        "verse_end": 17,
    },
}


def golden(group, name, wire):
    path = FIXTURES / group / f"{name}.json"
    if os.environ.get("BIBLELIVE_UPDATE_PROTOCOL_FIXTURES") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wire + "\n", encoding="utf-8")
    assert path.read_text(encoding="utf-8") == wire + "\n"


def test_action_fixtures():
    assert set(ACTIONS) == set(_OPERATOR_ACTION_ADAPTER.json_schema()["discriminator"]["mapping"])
    for action, example in ACTIONS.items():
        parsed = _OPERATOR_ACTION_ADAPTER.validate_python(example)
        wire = json.dumps(parsed.model_dump())
        assert wire == json.dumps(example)
        golden("actions", action, wire)
    assert set(ACTIONS) == {path.stem for path in (FIXTURES / "actions").glob("*.json")}


def test_real_server_protocol_fixtures(client, app_state, kjv_db, monkeypatch):
    class FixedDatetime:
        @staticmethod
        def now(_timezone):
            return datetime(2026, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(models, "datetime", FixedDatetime)
    monkeypatch.setattr(models.uuid, "uuid4", lambda: uuid.UUID(REFERENCE_ID))
    monkeypatch.setattr(config, "FOLLOW_ALONG", True)
    monkeypatch.setattr(
        app_state, "available_bible_versions", lambda: {"kjv": BibleVersion(id="kjv", path=kjv_db)}
    )
    adapters = {
        "operator": TypeAdapter(OperatorServerMessage),
        "display": TypeAdapter(DisplayServerMessage),
    }
    seen = {group: set() for group in adapters}

    def receive(socket, group, kind, *, save=True):
        wire = socket.receive_text()
        parsed = adapters[group].validate_json(wire)
        assert parsed.type == kind
        assert json.dumps(parsed.model_dump()) == wire
        if save:
            golden(group, kind, wire)
            seen[group].add(kind)

    def send(operator, action):
        operator.send_text(json.dumps(ACTIONS[action]))

    with client.websocket_connect("/ws/operator") as operator:
        receive(operator, "operator", "state_snapshot")
        send(operator, "authenticate")
        with client.websocket_connect("/ws/display") as display:
            receive(display, "display", "clear")
            receive(display, "display", "display_settings", save=False)
            receive(operator, "operator", "connection_status")
            response = client.post(
                "/api/reference",
                json={"book": "John", "chapter": 3, "verse_start": 16, "confidence": "high"},
            )
            assert response.status_code == 201
            receive(operator, "operator", "pending_update")
            send(operator, "approve")
            receive(operator, "operator", "approval_confirmed")
            receive(operator, "operator", "pending_update", save=False)
            receive(display, "display", "reference")

            send(operator, "display_reference")
            receive(operator, "operator", "display_confirmed")
            receive(display, "display", "reference", save=False)

            hint = FollowAlongHint(
                book="John", chapter=3, verse_start=18, verse_end=18, display_id=REFERENCE_ID
            )
            monkeypatch.setattr(
                app_state.verse_indexes, "for_language", AsyncMock(return_value=None)
            )
            monkeypatch.setattr(app_state.follow_along, "on_transcript", lambda *_: (hint, True))
            assert client.portal.call(app_state.offer_follow_along, "example transcript", "en")
            receive(operator, "operator", "follow_along")

            for action in ("set_verse_limit", "set_display_theme"):
                send(operator, action)
                receive(
                    operator,
                    "operator",
                    "display_settings_confirmed",
                    save=action == "set_display_theme",
                )
                receive(display, "display", "display_settings", save=action == "set_display_theme")
            send(operator, "set_auto_mode")
            receive(operator, "operator", "auto_mode_confirmed")
            send(operator, "set_follow_auto")
            receive(operator, "operator", "follow_auto_confirmed")
            assert client.portal.call(app_state.change_bible_version, "kjv") == "kjv"
            receive(operator, "operator", "bible_version_confirmed")
            operator.send_text("{}")
            receive(operator, "operator", "error")
            send(operator, "clear")
            receive(operator, "operator", "clear_confirmed")
            receive(display, "display", "clear", save=False)
            send(operator, "clear_pending")
            receive(operator, "operator", "pending_cleared")
            receive(operator, "operator", "pending_update", save=False)

    for group, adapter in adapters.items():
        assert seen[group] == set(adapter.json_schema()["discriminator"]["mapping"])
        assert seen[group] == {path.stem for path in (FIXTURES / group).glob("*.json")}


@pytest.mark.parametrize("message", [[], [1], 42, 1.5, "hello", None, True])
def test_non_object_operator_json_keeps_socket_open(client, message):
    with client.websocket_connect("/ws/operator") as operator:
        assert operator.receive_json()["type"] == "state_snapshot"
        operator.send_json(message)
        assert operator.receive_json() == {
            "type": "error",
            "data": {"message": "Missing 'action' field", "action": "unknown"},
        }
        operator.send_json({"action": "clear"})
        assert operator.receive_json() == {"type": "clear_confirmed", "data": {}}


@pytest.mark.parametrize("action", [["unknown"], {"name": "unknown"}, 42, True])
def test_unknown_action_error_preserves_original_value(client, action):
    with client.websocket_connect("/ws/operator") as operator:
        assert operator.receive_json()["type"] == "state_snapshot"
        operator.send_json({"action": action})
        wire = operator.receive_text()
        assert json.loads(wire) == {
            "type": "error",
            "data": {"message": f"Unknown action: {action}", "action": action},
        }
        parsed = TypeAdapter(OperatorServerMessage).validate_json(wire)
        assert json.dumps(parsed.model_dump()) == wire
