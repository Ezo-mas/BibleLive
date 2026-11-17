"""Launcher platform operations and shared browser/monitor values."""

import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

RunFn = Callable[..., "subprocess.CompletedProcess[bytes]"]
ShellExecuteFn = Callable[[str, str], int]


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

    def kill_tree(self, proc: subprocess.Popen[bytes], *, grace: float = 5.0) -> None: ...

    def child_process_options(self) -> dict[str, int]: ...

    def app_data_dir(self) -> Path: ...

    def models_dir(self) -> Path: ...

    def log_dir(self, data_dir: Path) -> Path: ...

    def firewall_applies(self) -> bool: ...

    def firewall_programs(self, executable: str) -> list[str]: ...

    def firewall_add_script(self, programs: Sequence[str], system32: str | None = None) -> str: ...

    def firewall_rule_exists(self, run: RunFn = ..., system32: str | None = None) -> bool: ...

    def add_firewall_rule(
        self,
        programs: Sequence[str],
        shell_execute: ShellExecuteFn | None = None,
        system32: str | None = None,
    ) -> bool: ...


def current(name: str | None = None) -> Platform:
    """Windows, macOS, or the generic POSIX fallback (Linux)."""
    name = sys.platform if name is None else name
    if name == "win32":
        from . import windows

        return windows
    if name == "darwin":
        from . import macos

        return macos
    from . import fallback

    return fallback
