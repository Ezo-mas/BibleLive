"""The pipeline's microphone peaks while live, read from the server's operator WebSocket.

While a session runs, ffmpeg in the audio pipeline holds the microphone, so the launcher's
own meter is off. The pipeline posts 50 ms peaks to the server, which forwards them to
operators as ``input_levels`` messages; this listens on 127.0.0.1 like an operator console
and keeps only those. The server does not announce operator connections and the console
ignores the operator count, so the connection changes nothing an operator sees.

The token goes only in the first message (never in the URL, which uvicorn logs), over a
socket opened here to 127.0.0.1 so no system proxy is ever involved.
"""

import json
import logging
import math
import socket
import threading
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)
# websockets logs every frame at DEBUG, the authenticate frame (with the token) included:
# give it its own logger that never goes below WARNING, whatever the root logger allows.
_socket_logger = logging.getLogger(f"{__name__}.socket")
_socket_logger.setLevel(logging.WARNING)

LOOPBACK = "127.0.0.1"
CONNECT_TIMEOUT = 1.0
RECV_TIMEOUT = 0.25  # how quickly the reader notices Stop
RETRY_DELAY = 1.0
MAX_LEVELS = 100  # 5 s of 50 ms peaks when nobody drains them
# The operator snapshot sent on connect carries the history; far above its real size.
MAX_MESSAGE_BYTES = 4 * 1024 * 1024


class LevelConnection(Protocol):
    def __enter__(self) -> "LevelConnection": ...
    def __exit__(self, *exc: Any) -> object: ...
    def send(self, message: str) -> None: ...
    def recv(self, timeout: float | None = None) -> str | bytes: ...


def connect_operator(port: int) -> LevelConnection:
    """Open the operator WebSocket on 127.0.0.1 (raises on failure)."""
    from websockets.sync.client import connect  # uvicorn[standard] dependency

    sock = socket.create_connection((LOOPBACK, port), timeout=CONNECT_TIMEOUT)
    try:
        # A caller-supplied socket keeps its timeout; the connection's reader needs none.
        sock.settimeout(None)
        connection: LevelConnection = connect(
            f"ws://{LOOPBACK}:{port}/ws/operator",
            sock=sock,
            open_timeout=CONNECT_TIMEOUT * 2,
            close_timeout=CONNECT_TIMEOUT,
            max_size=MAX_MESSAGE_BYTES,
            compression=None,
            logger=_socket_logger,
        )
    except BaseException:
        sock.close()
        raise
    return connection


def input_levels(raw: str | bytes) -> list[float]:
    """The peaks in one ``input_levels`` message; nothing for any other message."""
    try:
        message = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(message, dict) or message.get("type") != "input_levels":
        return []
    data = message.get("data")
    levels = data.get("levels") if isinstance(data, dict) else None
    if not isinstance(levels, list):
        return []
    return [
        min(1.0, max(0.0, float(level)))
        for level in levels
        if isinstance(level, int | float) and not isinstance(level, bool) and math.isfinite(level)
    ]


POLICY_VIOLATION = 1008  # the server's close code for a refused operator token


def _policy_violation(exc: BaseException) -> bool:
    """Whether the server closed the socket because it refused our token."""
    received = getattr(exc, "rcvd", None)  # websockets.exceptions.ConnectionClosed
    return getattr(received, "code", None) == POLICY_VIOLATION


class LiveLevels:
    """A background reader that reconnects quietly until stopped."""

    def __init__(
        self,
        *,
        connect: Callable[[int], LevelConnection] = connect_operator,
        retry_delay: float = RETRY_DELAY,
    ) -> None:
        self._connect = connect
        self._retry_delay = retry_delay
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._levels: deque[float] = deque(maxlen=MAX_LEVELS)
        self._thread: threading.Thread | None = None

    def start(self, port: int, token: str | None) -> None:
        with self._lock:
            self._stop.set()
            # Each reader keeps its own stop event and buffer, so one still closing
            # its socket after a Stop can never feed the next session.
            self._stop = stop = threading.Event()
            levels: deque[float] = deque(maxlen=MAX_LEVELS)
            self._levels = levels
            thread = threading.Thread(
                target=self._run, args=(port, token, stop, levels), daemon=True, name="live-levels"
            )
            self._thread = thread
        thread.start()

    def stop(self, timeout: float = 0.0) -> None:
        """Stop reading; wait up to ``timeout`` seconds for the reader to end."""
        with self._lock:
            self._stop.set()
            self._levels = deque(maxlen=MAX_LEVELS)
            thread, self._thread = self._thread, None
        if thread is not None and timeout > 0:
            thread.join(timeout)

    def drain(self) -> list[float]:
        with self._lock:
            levels = self._levels
            drained = [levels.popleft() for _ in range(len(levels))]
        return drained

    def _run(
        self, port: int, token: str | None, stop: threading.Event, levels: deque[float]
    ) -> None:
        while not stop.is_set():
            try:
                self._read(port, token, stop, levels)
            except Exception as exc:
                if _policy_violation(exc):
                    # The server refused our token. Retrying would only add a false
                    # auth-failure warning to its log every few seconds, all session.
                    logger.info("Live levels unavailable: the server refused the operator token")
                    return
                # Expected while the server starts or after it stops: retry quietly.
                logger.debug("Live level connection ended: %s", type(exc).__name__)
            if stop.wait(self._retry_delay):
                return

    def _read(
        self, port: int, token: str | None, stop: threading.Event, levels: deque[float]
    ) -> None:
        with self._connect(port) as connection:
            if token:
                connection.send(json.dumps({"action": "authenticate", "token": token}))
            while not stop.is_set():
                try:
                    raw = connection.recv(timeout=RECV_TIMEOUT)
                except TimeoutError:
                    continue
                received = input_levels(raw)
                if received and not stop.is_set():
                    levels.extend(received)
