"""Auto-approve mode: threshold filtering, persistence, and the WS action.

The mode is server-side state: references that arrive while it is on are
approved through the same path as a manual approval (so the display-send path
stays single), while references already pending are never touched. Persistence
lives in a small JSON file written atomically; every failure mode falls back to
the configured default instead of breaking the server.
"""

import asyncio
import json
import logging

from core.config import config
from core.server.settings_store import (
    DEFAULT_AUTO_MODE,
    SettingsStore,
    coerce_auto_mode,
    is_auto_mode,
)
from core.server.state import AppState

# NOTE: the ``client``, ``app_state`` and autouse ``reset_app_state`` fixtures
# come from tests/conftest.py. That fixture gives every test a fresh settings
# file and resets the in-memory mode to ``off``.


def _submit(client, confidence="high", book="John", chapter=3, verse_start=16):
    """Submit a reference over HTTP and return its reference_id."""
    response = client.post(
        "/api/reference",
        json={
            "book": book,
            "chapter": chapter,
            "verse_start": verse_start,
            "confidence": confidence,
            "inferred": False,
            "text": "",
        },
    )
    assert response.status_code == 201
    return response.json()["reference_id"]


def _set_auto_mode(client, auto_mode):
    """Set the mode over the operator WebSocket, without touching pending state."""
    with client.websocket_connect("/ws/operator") as websocket:
        assert json.loads(websocket.receive_text())["type"] == "state_snapshot"
        websocket.send_text(json.dumps({"action": "set_auto_mode", "auto_mode": auto_mode}))
        confirmed = json.loads(websocket.receive_text())
        assert confirmed["type"] == "auto_mode_confirmed"
        assert confirmed["data"] == {"auto_mode": auto_mode}


def _drain_display_greeting(display_ws):
    """Consume the two messages a fresh display client receives on connect."""
    assert json.loads(display_ws.receive_text())["type"] == "clear"
    assert json.loads(display_ws.receive_text())["type"] == "display_settings"


def _pending_ids(app_state):
    return [ref.id for ref in app_state.pending_references]


class TestAutoApproveThresholds:
    """Each mode approves exactly the confidences it promises."""

    def test_off_leaves_new_references_pending(self, client, app_state):
        assert app_state.auto_mode == DEFAULT_AUTO_MODE

        ref_id = _submit(client, confidence="low")

        assert _pending_ids(app_state) == [ref_id]
        assert app_state.current_display is None
        assert app_state.reference_history == []

    def test_all_auto_approves_low_confidence(self, client, app_state):
        _set_auto_mode(client, "all")

        ref_id = _submit(client, confidence="low")

        assert _pending_ids(app_state) == []
        assert app_state.current_display is not None
        assert app_state.current_display.id == ref_id
        assert [ref.id for ref in app_state.reference_history] == [ref_id]

    def test_high_auto_approves_only_high(self, client, app_state):
        _set_auto_mode(client, "high")

        low = _submit(client, confidence="low")
        medium = _submit(client, confidence="medium")
        high = _submit(client, confidence="high", verse_start=17)

        assert _pending_ids(app_state) == [low, medium]
        assert app_state.current_display is not None
        assert app_state.current_display.id == high
        assert [ref.id for ref in app_state.reference_history] == [high]

    def test_medium_and_up_auto_approves_high_and_medium(self, client, app_state):
        _set_auto_mode(client, "medium_and_up")

        low = _submit(client, confidence="low")
        medium = _submit(client, confidence="medium")
        high = _submit(client, confidence="high", verse_start=17)

        assert _pending_ids(app_state) == [low]
        # Newest approval is first in history.
        assert [ref.id for ref in app_state.reference_history] == [high, medium]

    def test_off_restores_manual_behavior(self, client, app_state):
        _set_auto_mode(client, "all")
        _set_auto_mode(client, "off")

        ref_id = _submit(client, confidence="low")

        assert _pending_ids(app_state) == [ref_id]
        assert app_state.current_display is None
        # Manual approval still works after auto mode was switched off again.
        assert client.post("/api/approve", json={"reference_id": ref_id}).status_code == 200
        assert app_state.current_display is not None


class TestPendingIsPreserved:
    """Turning auto mode on/off only affects references that arrive later."""

    def test_enabling_auto_mode_does_not_drain_existing_pending(self, client, app_state):
        first = _submit(client, confidence="low")
        second = _submit(client, confidence="high")

        _set_auto_mode(client, "all")

        assert _pending_ids(app_state) == [first, second]
        assert app_state.current_display is None
        assert app_state.reference_history == []

    def test_only_references_arriving_after_the_change_are_approved(self, client, app_state):
        first = _submit(client, confidence="high")
        second = _submit(client, confidence="high", verse_start=17)

        with client.websocket_connect("/ws/display") as display_ws:
            _drain_display_greeting(display_ws)

            _set_auto_mode(client, "all")

            # The two older detections are still waiting for the operator...
            assert _pending_ids(app_state) == [first, second]
            assert app_state.current_display is None

            # ...while the next detection is displayed without any operator action.
            third = _submit(client, confidence="high", verse_start=18)
            shown = json.loads(display_ws.receive_text())
            assert shown["type"] == "reference"
            assert shown["data"]["id"] == third

            assert _pending_ids(app_state) == [first, second]

    def test_mode_change_during_arrival_does_not_retroactively_approve(
        self, client, app_state, monkeypatch
    ):
        """The mode is captured at arrival, not re-read after the queue broadcast.

        Deterministic interleaving of the race: a mode change that lands while
        the arrival ``pending_update`` is in flight must not approve a reference
        that arrived while approval was off.
        """
        original = app_state._send_pending_update

        async def flip_mode_during_broadcast():
            # Runs at exactly the await inside add_reference, after the mode
            # must already have been captured.
            app_state.auto_mode = "all"
            await original()

        monkeypatch.setattr(app_state, "_send_pending_update", flip_mode_during_broadcast)

        ref_id = _submit(client, confidence="low")

        # The mode did change, but the reference keeps its arrival-time mode.
        assert app_state.auto_mode == "all"
        assert _pending_ids(app_state) == [ref_id]
        assert app_state.current_display is None

    def test_mode_change_sends_nothing_to_the_display(self, client, app_state):
        """Flipping the switch is operator-only; displays only ever see approvals."""
        with client.websocket_connect("/ws/display") as display_ws:
            _drain_display_greeting(display_ws)

            _set_auto_mode(client, "all")
            _set_auto_mode(client, "off")
            _set_auto_mode(client, "high")

            # A rejected non-reference message would arrive before this approve,
            # so receiving the approved reference first proves the socket stayed
            # silent through three mode changes.
            ref_id = _submit(client, confidence="low")
            approved = client.post("/api/approve", json={"reference_id": ref_id})
            assert approved.status_code == 200

            shown = json.loads(display_ws.receive_text())
            assert shown["type"] == "reference"
            assert shown["data"]["id"] == ref_id


class TestApprovalPathStaysSingle:
    """Auto-approval reuses the manual path; nothing else feeds the display."""

    def test_filtered_reference_never_reaches_the_display(self, client, app_state):
        with client.websocket_connect("/ws/display") as display_ws:
            _drain_display_greeting(display_ws)

            _set_auto_mode(client, "high")
            _submit(client, confidence="low")

            assert len(app_state.pending_references) == 1
            assert app_state.current_display is None

            # It only reaches the display once the operator approves it.
            pending_id = app_state.pending_references[0].id
            assert client.post("/api/approve", json={"reference_id": pending_id}).status_code == 200
            shown = json.loads(display_ws.receive_text())
            assert shown["type"] == "reference"
            assert shown["data"]["id"] == pending_id

    def test_auto_approval_calls_approve_and_notify(self, client, app_state, monkeypatch):
        """The single approval path is the one auto-approval goes through."""
        calls: list[str] = []
        original = app_state.approve_and_notify

        async def spy(reference_id: str):
            calls.append(reference_id)
            return await original(reference_id)

        monkeypatch.setattr(app_state, "approve_and_notify", spy)
        _set_auto_mode(client, "all")

        ref_id = _submit(client, confidence="low")

        assert calls == [ref_id]

    def test_display_send_only_fires_for_approved_references(self, client, app_state, monkeypatch):
        """``_send_display_reference`` is invoked by the approval, and only then."""
        sent: list[str] = []
        original = app_state._send_display_reference

        async def spy(ref):
            sent.append(ref.id)
            await original(ref)

        monkeypatch.setattr(app_state, "_send_display_reference", spy)
        _set_auto_mode(client, "high")

        _submit(client, confidence="low")
        assert sent == []

        approved_id = _submit(client, confidence="high", verse_start=17)
        assert sent == [approved_id]

    def test_operator_observes_arrival_then_confirmation_then_queue(self, client, app_state):
        """Auto-approval keeps the manual message order: confirm, then queue."""
        with (
            client.websocket_connect("/ws/display") as display_ws,
            client.websocket_connect("/ws/operator") as operator_ws,
        ):
            _drain_display_greeting(display_ws)
            snapshot = json.loads(operator_ws.receive_text())
            assert snapshot["type"] == "state_snapshot"
            assert snapshot["data"]["auto_mode"] == "off"

            operator_ws.send_text(json.dumps({"action": "set_auto_mode", "auto_mode": "high"}))
            assert json.loads(operator_ws.receive_text())["type"] == "auto_mode_confirmed"

            ref_id = _submit(client, confidence="high")

            # The reference arrives in the queue first...
            arrival = json.loads(operator_ws.receive_text())
            assert arrival["type"] == "pending_update"
            assert [ref["id"] for ref in arrival["data"]["pending"]] == [ref_id]

            # ...then the approval confirmation, then the queue without it.
            confirmed = json.loads(operator_ws.receive_text())
            assert confirmed["type"] == "approval_confirmed"
            assert confirmed["data"]["reference"]["id"] == ref_id
            empty = json.loads(operator_ws.receive_text())
            assert empty["type"] == "pending_update"
            assert empty["data"]["count"] == 0

            shown = json.loads(display_ws.receive_text())
            assert shown["type"] == "reference"
            assert shown["data"]["id"] == ref_id


class TestManualApprovalStillWorks:
    """Auto mode is a convenience, not a replacement for the operator."""

    def test_manual_approve_works_while_auto_mode_is_enabled(self, client, app_state):
        _set_auto_mode(client, "high")
        pending_id = _submit(client, confidence="medium")  # below the threshold

        with client.websocket_connect("/ws/operator") as websocket:
            snapshot = json.loads(websocket.receive_text())
            assert [ref["id"] for ref in snapshot["data"]["pending"]] == [pending_id]
            assert snapshot["data"]["auto_mode"] == "high"

            websocket.send_text(json.dumps({"action": "approve", "reference_id": pending_id}))
            assert json.loads(websocket.receive_text())["type"] == "approval_confirmed"
            assert json.loads(websocket.receive_text())["type"] == "pending_update"

        assert app_state.current_display is not None
        assert app_state.current_display.id == pending_id
        assert _pending_ids(app_state) == []


class TestSetAutoModeAction:
    """The operator action validates, confirms, and persists."""

    def test_snapshot_reports_the_mode(self, client):
        with client.websocket_connect("/ws/operator") as websocket:
            snapshot = json.loads(websocket.receive_text())
            assert snapshot["data"]["auto_mode"] == "off"

    def test_confirmed_message_and_http_state(self, client):
        with client.websocket_connect("/ws/operator") as websocket:
            assert json.loads(websocket.receive_text())["type"] == "state_snapshot"

            websocket.send_text(
                json.dumps({"action": "set_auto_mode", "auto_mode": "medium_and_up"})
            )
            confirmed = json.loads(websocket.receive_text())
            assert confirmed == {
                "type": "auto_mode_confirmed",
                "data": {"auto_mode": "medium_and_up"},
            }

        assert client.get("/api/state").json()["auto_mode"] == "medium_and_up"

    def test_invalid_mode_is_rejected_without_breaking_the_connection(self, client, app_state):
        with client.websocket_connect("/ws/operator") as websocket:
            assert json.loads(websocket.receive_text())["type"] == "state_snapshot"

            websocket.send_text(json.dumps({"action": "set_auto_mode", "auto_mode": "sometimes"}))
            error = json.loads(websocket.receive_text())
            assert error["type"] == "error"
            assert error["data"]["action"] == "set_auto_mode"
            assert app_state.auto_mode == "off"

            # The connection stays usable and the state is unchanged.
            websocket.send_text(json.dumps({"action": "set_auto_mode", "auto_mode": "high"}))
            confirmed = json.loads(websocket.receive_text())
            assert confirmed["type"] == "auto_mode_confirmed"
            assert confirmed["data"]["auto_mode"] == "high"

    def test_missing_mode_is_rejected(self, client, app_state):
        with client.websocket_connect("/ws/operator") as websocket:
            assert json.loads(websocket.receive_text())["type"] == "state_snapshot"

            websocket.send_text(json.dumps({"action": "set_auto_mode"}))
            error = json.loads(websocket.receive_text())
            assert error["type"] == "error"
            assert error["data"]["action"] == "set_auto_mode"
            assert app_state.auto_mode == "off"


class TestPersistence:
    """Precedence: persisted file, then the environment, then off."""

    def test_persisted_mode_survives_appstate_recreation(self, client, app_state):
        _set_auto_mode(client, "medium_and_up")
        settings_path = app_state.settings_store.path
        assert settings_path.is_file()

        recreated = AppState(app_state.bible_lookup, SettingsStore(settings_path))

        assert recreated.auto_mode == "medium_and_up"

    def test_persisted_mode_wins_over_the_environment_default(self, app_state, monkeypatch):
        app_state.settings_store.save_auto_mode("high")
        monkeypatch.setattr(config, "AUTO_MODE", "all")

        recreated = AppState(app_state.bible_lookup, SettingsStore(app_state.settings_store.path))

        assert recreated.auto_mode == "high"

    def test_environment_default_used_when_nothing_is_persisted(
        self, app_state, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(config, "AUTO_MODE", "medium_and_up")

        recreated = AppState(app_state.bible_lookup, SettingsStore(tmp_path / "fresh.json"))

        assert recreated.auto_mode == "medium_and_up"

    def test_invalid_environment_default_falls_back_to_off(self, app_state, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "AUTO_MODE", "sometimes")

        recreated = AppState(app_state.bible_lookup, SettingsStore(tmp_path / "fresh.json"))

        assert recreated.auto_mode == "off"

    def test_malformed_settings_file_falls_back_to_the_environment(
        self, app_state, tmp_path, caplog
    ):
        path = tmp_path / "server_settings.json"
        path.write_text("{not json at all", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            recreated = AppState(app_state.bible_lookup, SettingsStore(path))

        assert recreated.auto_mode == "off"
        assert "settings file" in caplog.text.lower()

    def test_settings_file_with_invalid_mode_falls_back(self, app_state, tmp_path, caplog):
        path = tmp_path / "server_settings.json"
        path.write_text(json.dumps({"auto_mode": "sometimes"}), encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            recreated = AppState(app_state.bible_lookup, SettingsStore(path))

        assert recreated.auto_mode == "off"
        assert "invalid auto-approve mode" in caplog.text.lower()

    def test_settings_file_that_is_not_an_object_falls_back(self, app_state, tmp_path, caplog):
        path = tmp_path / "server_settings.json"
        path.write_text(json.dumps(["all"]), encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            recreated = AppState(app_state.bible_lookup, SettingsStore(path))

        assert recreated.auto_mode == "off"
        assert "expected a json object" in caplog.text.lower()

    def test_write_failure_keeps_the_mode_in_memory(self, app_state, tmp_path, caplog):
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, not a directory", encoding="utf-8")
        broken = SettingsStore(blocker / "server_settings.json")
        state = AppState(app_state.bible_lookup, broken)

        with caplog.at_level(logging.WARNING):
            assert asyncio.run(state.set_auto_mode("all")) is True

        assert state.auto_mode == "all"
        assert "could not persist settings" in caplog.text.lower()


class TestSettingsStore:
    """The store itself: atomic writes and tolerant reads."""

    def test_creates_parent_directories(self, tmp_path):
        store = SettingsStore(tmp_path / "nested" / "settings.json")

        assert store.save_auto_mode("high") is True
        assert json.loads(store.path.read_text(encoding="utf-8")) == {"auto_mode": "high"}

    def test_atomic_write_leaves_no_temporary_files(self, tmp_path):
        store = SettingsStore(tmp_path / "server_settings.json")

        store.save_auto_mode("all")
        store.save_auto_mode("off")

        assert store.load_auto_mode() == "off"
        assert [path.name for path in tmp_path.iterdir()] == ["server_settings.json"]

    def test_unknown_keys_are_preserved(self, tmp_path):
        path = tmp_path / "server_settings.json"
        path.write_text(json.dumps({"other_setting": 7, "auto_mode": "off"}), encoding="utf-8")

        SettingsStore(path).save_auto_mode("high")

        assert json.loads(path.read_text(encoding="utf-8")) == {
            "other_setting": 7,
            "auto_mode": "high",
        }

    def test_missing_file_loads_the_default(self, tmp_path):
        assert SettingsStore(tmp_path / "absent.json").load_auto_mode() == "off"
        assert SettingsStore(tmp_path / "absent.json").load_auto_mode(default="all") == "all"

    def test_mode_helpers(self):
        assert is_auto_mode("medium_and_up") is True
        assert is_auto_mode("sometimes") is False
        assert is_auto_mode(1) is False
        assert coerce_auto_mode("high") == "high"
        assert coerce_auto_mode("HIGH") == "off"
        assert coerce_auto_mode(None) == "off"
