"""Display-safety and operator-notification contracts for the HTTP API.

Two properties the audience-facing display and the operator console rely on:

* what reaches the display is always database text for a real passage, never
  text or coordinates a caller made up (test 3);
* the HTTP ``clear`` / ``clear-pending`` endpoints tell operators what the
  WebSocket actions of the same name tell them (test 13).

Both were defects until ingest validation (F1) and the HTTP clear notifications
(F7a) landed; these tests now guard them as regressions. Harness problems (a
missing message, an unexpected status) use ``pytest.fail`` / ``TimeoutError``,
so they read differently from a broken contract assertion.
"""

import json
import threading
import time

import pytest

from core.config import config

pytestmark = pytest.mark.integration

AUTO_MODES = ["off", "high", "medium_and_up", "all"]

# Each body is something the display must never show as submitted. All carry
# "high" confidence so every auto mode treats them as approvable.
BAD_BODIES = {
    "fake_book": {
        "book": "Announcement",
        "chapter": 1,
        "verse_start": 1,
        "confidence": "high",
        "text": "INJECTED",
    },
    "nonexistent_chapter": {
        "book": "John",
        "chapter": 99,
        "verse_start": 1,
        "confidence": "high",
        "text": "INJECTED",
    },
    "nonexistent_verse": {
        "book": "John",
        "chapter": 3,
        "verse_start": 99,
        "confidence": "high",
        "text": "INJECTED",
    },
    "reversed_range": {
        "book": "John",
        "chapter": 3,
        "verse_start": 16,
        "verse_end": 10,
        "confidence": "high",
    },
}

SENTINEL = {
    "book": "Genesis",
    "chapter": 1,
    "verse_start": 1,
    "confidence": "high",
}


READ_TIMEOUT_S = 10.0


def _recv(ws, timeout: float = READ_TIMEOUT_S) -> dict:
    """Receive one JSON message, or raise ``TimeoutError`` after ``timeout`` seconds.

    Starlette's TestClient ``receive_text`` has no timeout and would hang CI
    forever on a missing message. The blocking call runs in a daemon thread that
    is abandoned on timeout (the test is failing anyway, and leaving the
    ``with`` block closes the socket).
    """
    outcome: dict = {}

    def receive() -> None:
        try:
            outcome["value"] = ws.receive_text()
        except BaseException as error:  # re-raised in the caller's thread
            outcome["error"] = error

    reader = threading.Thread(target=receive, daemon=True, name="ws-receive")
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        raise TimeoutError(f"no WebSocket message within {timeout}s")
    if "error" in outcome:
        raise outcome["error"]
    return json.loads(outcome["value"])


def _expect_type(ws, expected: str) -> dict:
    """Receive one message that must be ``expected`` (a harness precondition)."""
    message = _recv(ws)
    if message["type"] != expected:
        pytest.fail(f"expected a {expected!r} message, got {message['type']!r}")
    return message


def _read_until(ws, predicate, timeout: float = READ_TIMEOUT_S) -> list[dict]:
    """Read messages until one satisfies ``predicate``; return all read, last included.

    Callers only use this after triggering a message that is guaranteed to
    come (a sentinel); the overall ``timeout`` turns a missing one into
    ``TimeoutError`` instead of a hang.
    """
    deadline = time.monotonic() + timeout
    messages = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"expected message never arrived; saw {_types(messages)}")
        message = _recv(ws, remaining)
        messages.append(message)
        if predicate(message):
            return messages


def _operator_sentinel(operator_ws) -> list[dict]:
    """Return every operator message produced so far, ending at a known marker.

    An unknown action is answered with an ``error`` message, so sending one
    after the action under test turns "has everything arrived yet?" into a read
    that is guaranteed to end. Operator messages are ordered on one socket, so
    everything before the ``error`` was emitted before the marker was.
    """
    operator_ws.send_text(json.dumps({"action": "nope"}))
    messages = _read_until(operator_ws, lambda m: m["type"] == "error")
    return messages[:-1]


def _submit(client, body: dict) -> str | None:
    """POST a reference; return its id, or None when the server rejected it."""
    response = client.post("/api/reference", json=body)
    if response.status_code == 201:
        return response.json()["reference_id"]
    if 400 <= response.status_code < 500:
        return None  # rejected: a fine outcome for a bad body
    pytest.fail(f"unexpected status {response.status_code} from /api/reference: {response.text}")


def _db_text(app_state, ref: dict) -> str:
    verses = app_state.bible_lookup.get_verse_range(
        ref["book"], ref["chapter"], ref["verse_start"], ref.get("verse_end")
    )
    return " ".join(text for _, text in verses)


@pytest.mark.parametrize("auto_mode", AUTO_MODES)
@pytest.mark.parametrize("case", list(BAD_BODIES))
def test_caller_text_or_invalid_coordinates_never_reach_display(client, app_state, case, auto_mode):
    """A bad submission is rejected or held, never displayed as submitted.

    Invariant, deliberately fix-agnostic: every ``reference`` message the display
    receives before a known-good sentinel must (a) not contain caller text,
    (b) not have ``verse_end < verse_start`` and (c) carry exactly the database
    text for its label. Before ingest validation every case and mode failed it
    (the bad reference was displayed verbatim, directly under auto-approve or
    after the operator approved it). It holds whether the server rejects the
    submission (as it now does, with a 4xx), keeps it pending without
    displaying it, or displays a corrected passage backed by the database, so
    it guards the display rather than one particular status code (those are
    pinned in ``test_api_validation.py``).

    Under mode "off" the test plays the operator and approves what was queued,
    because that is exactly the step a human takes on a detection that looks
    plausible in the console.
    """
    app_state.auto_mode = auto_mode
    body = BAD_BODIES[case]

    with client.websocket_connect("/ws/display") as display_ws:
        _expect_type(display_ws, "clear")
        _expect_type(display_ws, "display_settings")

        reference_id = _submit(client, body)
        if reference_id is not None and auto_mode == "off":
            approve = client.post("/api/approve", json={"reference_id": reference_id})
            if approve.status_code >= 500 or approve.status_code < 200:
                pytest.fail(f"unexpected status {approve.status_code} from /api/approve")
            # 200: displayed (checked below); any 4xx: refused, so not displayed.

        # Sentinel: a valid reference that always reaches the display.
        sentinel_id = _submit(client, SENTINEL)
        if sentinel_id is None:
            pytest.fail("the valid Genesis 1:1 sentinel was rejected")
        if auto_mode == "off":
            sentinel_approval = client.post("/api/approve", json={"reference_id": sentinel_id})
            if sentinel_approval.status_code != 200:
                pytest.fail(f"sentinel approval failed: {sentinel_approval.status_code}")

        messages = _read_until(
            display_ws,
            lambda m: m["type"] == "reference" and m["data"]["book"] == "Genesis",
        )

    displayed = [m["data"] for m in messages[:-1] if m["type"] == "reference"]
    for ref in displayed:
        assert "INJECTED" not in json.dumps(ref), f"caller text reached the display: {ref}"
        end = ref.get("verse_end")
        assert end is None or end >= ref["verse_start"], f"reversed range displayed: {ref}"
        expected = _db_text(app_state, ref)
        assert expected, f"{ref['book']} {ref['chapter']}:{ref['verse_start']} is not in the Bible"
        assert ref["text"] == expected, f"display text is not the database text: {ref}"


@pytest.fixture
def operator_and_display(client):
    """Connected operator and display sockets with their initial messages consumed."""
    with (
        client.websocket_connect("/ws/display") as display_ws,
        client.websocket_connect("/ws/operator") as operator_ws,
    ):
        _expect_type(display_ws, "clear")
        _expect_type(display_ws, "display_settings")
        _expect_type(operator_ws, "state_snapshot")
        yield operator_ws, display_ws


def _show_genesis(client, operator_ws, display_ws) -> None:
    """Put Genesis 1:1 on the display and consume the messages that causes."""
    reference_id = _submit(client, SENTINEL)
    if reference_id is None:
        pytest.fail("Genesis 1:1 was rejected")
    _expect_ok(client.post("/api/approve", json={"reference_id": reference_id}))
    _expect_type(display_ws, "reference")
    _operator_sentinel(operator_ws)


def _types(messages: list[dict]) -> list[str]:
    return [m["type"] for m in messages]


def _pending_cleared_contract(client, operator_ws, clear_pending) -> None:
    for verse in (1, 2):
        _submit(client, {**SENTINEL, "verse_start": verse})
    _operator_sentinel(operator_ws)  # consume the arrival updates

    clear_pending()
    messages = _operator_sentinel(operator_ws)

    types = _types(messages)
    assert "pending_cleared" in types, f"operator was not told the queue was cleared: {types}"
    updates = [i for i, m in enumerate(messages) if m["type"] == "pending_update"]
    assert updates, f"operator got no pending_update: {types}"
    assert types.index("pending_cleared") < updates[-1]
    assert messages[updates[-1]]["data"]["count"] == 0


def test_ws_clear_notifies_operator_and_display(client, operator_and_display):
    """Parity baseline: the WS ``clear`` action confirms to operators and clears displays.

    This also proves the sentinel helper works against a real exchange, so a
    failure in the HTTP tests below points at the endpoint, not the helper.
    """
    operator_ws, display_ws = operator_and_display
    _show_genesis(client, operator_ws, display_ws)

    operator_ws.send_text(json.dumps({"action": "clear"}))
    messages = _operator_sentinel(operator_ws)

    assert "clear_confirmed" in _types(messages)
    _expect_type(display_ws, "clear")
    assert client.get("/api/state").json()["current_display"] is None


def test_ws_clear_pending_notifies_operator_in_order(client, operator_and_display):
    """Parity baseline: WS ``clear_pending`` sends ``pending_cleared`` then an empty update."""
    operator_ws, _ = operator_and_display
    _pending_cleared_contract(
        client,
        operator_ws,
        lambda: operator_ws.send_text(json.dumps({"action": "clear_pending"})),
    )


def test_http_clear_notifies_operator_like_ws_clear(client, operator_and_display):
    """HTTP ``/api/clear`` must tell operators ``clear_confirmed`` like the WS action.

    Otherwise a console that did not send the clear (a second operator, or a
    script driving the HTTP API) keeps showing a passage as live after it left
    the screen.
    """
    operator_ws, display_ws = operator_and_display
    _show_genesis(client, operator_ws, display_ws)

    _expect_ok(client.post("/api/clear"))
    if client.get("/api/state").json()["current_display"] is not None:
        pytest.fail("current_display not cleared by /api/clear")
    _expect_type(display_ws, "clear")

    messages = _operator_sentinel(operator_ws)
    assert "clear_confirmed" in _types(messages), (
        f"operator was not told the display was cleared: {_types(messages)}"
    )


def test_http_clear_pending_notifies_operator_like_ws_clear_pending(client, operator_and_display):
    """HTTP ``/api/clear-pending`` must send ``pending_cleared`` before the empty update."""
    operator_ws, _ = operator_and_display
    _pending_cleared_contract(
        client,
        operator_ws,
        lambda: _expect_ok(client.post("/api/clear-pending")),
    )


def _expect_ok(response) -> None:
    if response.status_code != 200:
        pytest.fail(f"unexpected status {response.status_code}: {response.text}")


def test_auth_is_off_for_these_tests():
    """The HTTP calls above are unauthenticated; fail loudly if the default changes."""
    assert config.REQUIRE_AUTH is False
