"""Launcher platform operations and shared browser/monitor values."""

import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Browser:
    path: str
    kind: str  # "edge" | "chrome"


@dataclass(frozen=True)
class Monitor:
    device: str  # e.g. "\\\\.\\DISPLAY2"
    x: int
    y: int
    width: int
    height: int
    primary: bool

    @property
    def label(self) -> str:
        name = self.device.lstrip("\\.") or self.device
        return f"{name} ({self.width}x{self.height}){' *' if self.primary else ''}"


class Platform(Protocol):
    def find_browser(self, env: Mapping[str, str] | None = None) -> Browser | None: ...

    def build_command(
        self, browser: Browser, url: str, monitor: Monitor, profile_dir: Path
    ) -> list[str]: ...

    def open_projector(
        self,
        browser: Browser,
        url: str,
        monitor: Monitor,
        profile_dir: Path,
        *,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> subprocess.Popen[bytes]: ...

    def find_profile_processes(self, profile_dir: Path) -> list[int]: ...

    def kill_pid_tree(self, pid: int) -> None: ...

    def enumerate_monitors(self) -> list[Monitor]: ...


def current() -> Platform:
    """Select Windows or the existing fallback; future platforms fit beside them."""
    if sys.platform == "win32":
        from . import windows

        return windows
    from . import fallback

    return fallback
