"""Existing non-Windows launcher behaviour, without native platform support."""

import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import Browser, Monitor

# These operations previously ran the same code on every platform.
from .windows import add_firewall_rule as add_firewall_rule
from .windows import build_command as build_command
from .windows import find_browser as find_browser
from .windows import firewall_add_script as firewall_add_script
from .windows import firewall_programs as firewall_programs
from .windows import firewall_rule_exists as firewall_rule_exists
from .windows import kill_pid_tree as kill_pid_tree
from .windows import log_dir as log_dir


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


def kill_tree(proc: subprocess.Popen[bytes], *, grace: float = 5.0) -> None:
    if sys.platform != "win32":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            if proc.poll() is None:
                proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=grace)
        # The leader may have exited while ffmpeg ignored TERM. Always finish the group
        # before a replacement can take the microphone/GPU.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            if proc.poll() is None:
                proc.kill()
        proc.wait(timeout=grace)


def child_process_options() -> dict[str, int]:
    extra: dict[str, int] = {}
    extra["start_new_session"] = True
    return extra


def app_data_dir() -> Path:
    return Path.home() / ".biblelive"


def models_dir() -> Path:
    return Path.home() / ".biblelive" / "models"


def firewall_applies() -> bool:
    return False
