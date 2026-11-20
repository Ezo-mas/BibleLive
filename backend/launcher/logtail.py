"""Read newly appended child-process log lines on demand."""

import os
import re
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from .i18n import t

# CSI (including uvicorn's colours) and OSC terminal escape sequences.
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
_LEVEL = r"DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL|FATAL|TRACE"
_AUDIO = re.compile(
    rf"^\d{{4}}-\d{{2}}-\d{{2}} (?P<time>\d{{2}}:\d{{2}}:\d{{2}})[,.]\d+ - (?P<level>{_LEVEL}) - "
)
_PREFIX = re.compile(rf"^(?P<level>{_LEVEL})(?::\s|\s+[^\s:]+:\s)")
_HEADER = re.compile(r"^=== \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ")


class LogLine(TypedDict):
    time: str
    source: str
    level: str
    text: str


class LogBuffer:
    def __init__(self, clock: Callable[[], float] = time.time, limit: int = 500) -> None:
        self._clock = clock
        self._lines: deque[LogLine] = deque(maxlen=limit)
        self._skipped = 0

    def append(self, source: str, level: str, text: str, stamp: str | None = None) -> None:
        if len(self._lines) == self._lines.maxlen:
            self._skipped += 1
        self._lines.append(
            LogLine(
                time=stamp or time.strftime("%H:%M:%S", time.localtime(self._clock())),
                source=source,
                level=level,
                text=text,
            )
        )

    def drain(self) -> list[LogLine]:
        lines = list(self._lines)
        self._lines.clear()
        if self._skipped:
            skipped, self._skipped = self._skipped, 0
            self.append("launcher", "WARNING", t("log.lines_skipped", count=skipped))
            lines.append(self._lines.pop())
        return lines


class _FileTail:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._partial = b""
        try:
            stat = path.stat()
        except OSError:
            pass
        else:
            self._identity = (stat.st_dev, stat.st_ino)
            self._offset = stat.st_size

    def read(self, buffer: LogBuffer) -> None:
        try:
            with self._path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if self._identity != identity or stat.st_size < self._offset:
                    self._identity = identity
                    self._offset = 0
                    self._partial = b""
                stream.seek(self._offset)
                for raw in stream:
                    self._offset += len(raw)
                    if self._partial:
                        raw = self._partial + raw
                        self._partial = b""
                    if not raw.endswith(b"\n"):
                        self._partial = raw
                        break
                    text = _ANSI.sub("", raw.decode("utf-8", errors="replace")).rstrip("\r\n")
                    if not text or _HEADER.match(text):
                        continue
                    audio = _AUDIO.match(text)
                    match = audio or _PREFIX.match(text)
                    level = match["level"] if match else "INFO"
                    level = {
                        "WARN": "WARNING",
                        "CRITICAL": "ERROR",
                        "FATAL": "ERROR",
                        "TRACE": "DEBUG",
                    }.get(level, level)
                    buffer.append(self._path.stem, level, text, audio["time"] if audio else None)
        except OSError:
            # The child may have yet to create the file, or be rotating it.
            pass


class LogTail:
    def __init__(self, directory: Path, buffer: LogBuffer) -> None:
        self._buffer = buffer
        self._files = [_FileTail(directory / f"{source}.log") for source in ("server", "audio")]

    def read(self) -> None:
        for file in self._files:
            file.read(self._buffer)

    def stop(self) -> None:
        self.read()
        self._files.clear()
