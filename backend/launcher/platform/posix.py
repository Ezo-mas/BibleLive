"""Process and firewall operations shared by macOS and the generic (Linux) fallback."""

import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import Browser, Monitor, RunFn, ShellExecuteFn
from .chromium import build_command


def bring_projector_to_front(profile_dir: Path) -> bool:
    del profile_dir
    return False


def open_projector(
    browser: Browser,
    url: str,
    monitor: Monitor,
    profile_dir: Path,
    *,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> subprocess.Popen[bytes]:
    # Its own session, so the browser is the leader of a group kill_pid_tree can end.
    return popen(
        build_command(browser, url, monitor, profile_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def kill_pid_tree(pid: int) -> None:
    """Force-kill ``pid`` and its process group (the projector browser runs as a leader)."""
    if sys.platform == "win32":  # never selected there; keeps mypy off os.killpg on Windows
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


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
    return {"start_new_session": True}


def log_dir(data_dir: Path) -> Path:
    return data_dir / "logs"


# No firewall rule outside Windows: the launcher shows no firewall row (firewall_applies is
# False), so the rest are never called there and only satisfy the Platform protocol.


def firewall_applies() -> bool:
    return False


def firewall_programs(executable: str) -> list[str]:
    del executable
    return []


def firewall_add_script(programs: Sequence[str], system32: str | None = None) -> str:
    del programs, system32
    return ""


def firewall_rule_exists(run: RunFn = subprocess.run, system32: str | None = None) -> bool:
    del run, system32
    return False


def add_firewall_rule(
    programs: Sequence[str],
    shell_execute: ShellExecuteFn | None = None,
    system32: str | None = None,
) -> bool:
    del programs, shell_execute, system32
    return False
