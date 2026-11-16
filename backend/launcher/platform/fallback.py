"""Existing non-Windows launcher behaviour, without native platform support."""

import subprocess
from collections.abc import Callable
from pathlib import Path

from . import Browser, Monitor

# These operations previously ran the same code on every platform.
from .windows import build_command as build_command
from .windows import find_browser as find_browser
from .windows import kill_pid_tree as kill_pid_tree


def enumerate_monitors() -> list[Monitor]:
    return []


def find_profile_processes(profile_dir: Path) -> list[int]:
    del profile_dir
    return []


def open_projector(
    browser: Browser,
    url: str,
    monitor: Monitor,
    profile_dir: Path,
    *,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> subprocess.Popen[bytes]:
    extra: dict[str, int] = {}
    extra["start_new_session"] = True
    return popen(
        build_command(browser, url, monitor, profile_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **extra,
    )
