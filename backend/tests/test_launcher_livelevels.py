"""The launcher's live wave source: pipeline peaks read from the operator WebSocket."""

import json
import logging
import queue
import threading
import time

import pytest
from launcher import livelevels
from launcher.livelevels import LiveLevels, input_levels


def wait_for(check):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if value := check():
            return value
        time.sleep(0.01)
    pytest.fail("Timed out waiting for the live level reader")


def levels_message(*levels):
    return json.dumps({"type": "input_levels", "data": {"levels": list(levels)}})


class FakeConnection:
    """Scripted operator socket: recv() hands out queued messages, then times out."""

    def __init__(self, messages=()):
        self.sent = []
        self.inbox = queue.Queue()
        for message in messages:
            self.inbox.put(message)
        self.exited = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.exited.set()

    def send(self, message):
        self.sent.append(json.loads(message))

    def recv(self, timeout=None):
        try:
            message = self.inbox.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None
        if isinstance(message, Exception):
            raise message
        return message


def drain_until(live, count):
    received = []

    def enough():
        received.extend(live.drain())
        return len(received) >= count

    wait_for(enough)
    return received


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (levels_message(0.0, 0.5, 1.0), [0.0, 0.5, 1.0]),
        (levels_message(-1, 2, True, "0.5", float("nan")), [0.0, 1.0]),
        (json.dumps({"type": "state_snapshot", "data": {"levels": [0.5]}}), []),
        (json.dumps({"type": "input_levels", "data": {"levels": "loud"}}), []),
        (json.dumps({"type": "input_levels"}), []),
        (json.dumps([0.5]), []),
        ("not json", []),
        (levels_message(0.25).encode(), [0.25]),
    ],
)
def test_only_input_levels_messages_count(raw, expected):
    assert input_levels(raw) == expected


def test_authenticates_first_and_keeps_only_levels():
    connection = FakeConnection(
        [
            json.dumps({"type": "state_snapshot", "data": {"pending": []}}),
            levels_message(0.1, 0.2),
            json.dumps({"type": "pending_update", "data": {}}),
            "garbage",
            levels_message(0.3),
        ]
    )
    live = LiveLevels(connect=lambda port: connection)
    live.start(8123, "secret")
    try:
        assert drain_until(live, 3) == [0.1, 0.2, 0.3]
        assert connection.sent == [{"action": "authenticate", "token": "secret"}]
    finally:
        live.stop(timeout=2)


def test_sends_nothing_without_a_token():
    connection = FakeConnection([levels_message(0.4)])
    ports = []
    live = LiveLevels(connect=lambda port: ports.append(port) or connection)
    live.start(8123, None)
    try:
        assert drain_until(live, 1) == [0.4]
        assert connection.sent == []
        assert ports == [8123]
    finally:
        live.stop(timeout=2)


def test_retries_quietly_until_the_server_answers_and_after_it_drops(caplog):
    attempts = []
    later = FakeConnection([levels_message(0.6)])
    dropped = FakeConnection([levels_message(0.5), ConnectionResetError("server went away")])

    def connect(port):
        attempts.append(port)
        if len(attempts) <= 2:
            raise ConnectionRefusedError("server still starting")
        return dropped if len(attempts) == 3 else later

    live = LiveLevels(connect=connect, retry_delay=0.01)
    caplog.set_level("INFO", logger=livelevels.__name__)
    live.start(8123, "secret")
    try:
        assert drain_until(live, 2) == [0.5, 0.6]
        assert dropped.exited.is_set()
        assert len(attempts) == 4
    finally:
        live.stop(timeout=2)
    assert caplog.records == []  # reconnecting is routine: nothing above debug
    assert "secret" not in caplog.text


def test_a_refused_token_stops_the_reader_instead_of_retrying(caplog):
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    attempts = []
    refused = ConnectionClosedError(Close(1008, "Unauthorized"), None)

    def connect(port):
        attempts.append(port)
        return FakeConnection([refused])

    live = LiveLevels(connect=connect, retry_delay=0.01)
    caplog.set_level("INFO", logger=livelevels.__name__)
    live.start(8123, "wrong")
    thread = live._thread
    assert thread is not None
    thread.join(2)
    assert not thread.is_alive()
    assert attempts == [8123]  # no retry loop filling the server's auth-failure log
    assert "refused the operator token" in caplog.text
    assert "wrong" not in caplog.text


def test_stop_ends_the_reader_and_drops_unread_levels():
    connection = FakeConnection([levels_message(0.9)])
    live = LiveLevels(connect=lambda port: connection)
    live.start(8123, None)
    wait_for(lambda: connection.inbox.empty())
    thread = live._thread
    live.stop(timeout=2)
    assert thread is not None and not thread.is_alive()
    assert connection.exited.is_set()
    assert live.drain() == []
    connection.inbox.put(levels_message(0.8))  # nobody reads any more
    assert live.drain() == []


def test_a_restart_never_mixes_in_the_previous_session():
    first = FakeConnection()
    second = FakeConnection([levels_message(0.3)])
    connections = iter([first, second])
    live = LiveLevels(connect=lambda port: next(connections))
    live.start(8123, None)
    wait_for(lambda: live._thread is not None)
    live.start(8124, None)
    try:
        first.inbox.put(levels_message(0.99))
        assert drain_until(live, 1) == [0.3]
        assert wait_for(first.exited.is_set)
    finally:
        live.stop(timeout=2)


def test_buffer_is_bounded_when_nobody_polls():
    connection = FakeConnection([levels_message(*[0.5] * 20) for _ in range(10)])
    live = LiveLevels(connect=lambda port: connection)
    live.start(8123, None)
    try:
        wait_for(lambda: connection.inbox.empty())
        time.sleep(0.05)
        assert len(live.drain()) == livelevels.MAX_LEVELS
    finally:
        live.stop(timeout=2)


def test_real_operator_socket_bypasses_proxies_and_keeps_the_token_out_of_the_url(
    monkeypatch, caplog
):
    server_module = pytest.importorskip("websockets.sync.server")
    # Even with every logger at DEBUG, the client's frame logging must not show the token.
    caplog.set_level("DEBUG")
    quiet_server = logging.getLogger("test.livelevels.server")
    quiet_server.setLevel(logging.WARNING)
    # A system proxy must never see this connection (it would see the token).
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    seen = []
    done = threading.Event()

    def handler(websocket):
        seen.append((websocket.request.path, json.loads(websocket.recv(timeout=2))))
        websocket.send(json.dumps({"type": "state_snapshot", "data": {}}))
        websocket.send(levels_message(0.25, 0.75))
        done.wait(3)

    with server_module.serve(handler, "127.0.0.1", 0, logger=quiet_server) as server:
        port = server.socket.getsockname()[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        live = LiveLevels()
        live.start(port, "secret")
        try:
            assert drain_until(live, 2) == [0.25, 0.75]
        finally:
            done.set()
            live.stop(timeout=3)
            server.shutdown()
    assert seen == [("/ws/operator", {"action": "authenticate", "token": "secret"})]
    assert "secret" not in caplog.text
