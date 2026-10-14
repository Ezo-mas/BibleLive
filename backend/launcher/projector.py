"""Open the audience display full-screen on the projector monitor (Edge/Chrome kiosk)."""

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .monitors import Monitor
from .process import kill_tree
from .settings import app_data_dir

logger = logging.getLogger(__name__)


class ProjectorError(Exception):
    """``code`` is "no_monitor" or "launch_failed" (the GUI translates it)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Browser:
    path: str
    kind: str  # "edge" | "chrome"


def display_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/display"


def operator_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/operator"


def default_profile_dir() -> Path:
    return app_data_dir() / "display-browser"


def find_browser(env: Mapping[str, str] | None = None) -> Browser | None:
    """Microsoft Edge first, then Chrome: well-known install dirs, then PATH."""
    env = os.environ if env is None else env
    roots = [env.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    candidates = (
        ("edge", ("Microsoft", "Edge", "Application", "msedge.exe"), "msedge"),
        ("chrome", ("Google", "Chrome", "Application", "chrome.exe"), "chrome"),
    )
    for kind, parts, exe in candidates:
        for root in roots:
            if root:
                p = Path(root).joinpath(*parts)
                if p.is_file():
                    return Browser(str(p), kind)
        found = shutil.which(exe)
        if found:
            return Browser(found, kind)
    return None


def build_command(browser: Browser, url: str, monitor: Monitor, profile_dir: Path) -> list[str]:
    """Pure: the kiosk command line placing a full-screen window on ``monitor``."""
    cmd = [
        browser.path,
        f"--app={url}",
        "--kiosk",
        "--no-first-run",
        f"--window-position={monitor.x},{monitor.y}",
        f"--user-data-dir={profile_dir}",
    ]
    if browser.kind == "edge":
        cmd.insert(3, "--edge-kiosk-type=fullscreen")
    return cmd


def find_profile_processes(profile_dir: Path) -> list[int]:
    """PIDs of processes whose command line mentions ``profile_dir`` (Windows only).

    A kiosk browser left over from a crashed run keeps our private profile locked, so a new
    launch would hand off to it and exit. Only the profile path is matched, so the user's
    normal browser (default profile) is never touched.
    """
    if sys.platform != "win32":
        return []
    script = (
        "Get-CimInstance Win32_Process | Where-Object "
        "{ $_.CommandLine -and $_.CommandLine.Contains($env:BL_PROFILE) } | "
        "ForEach-Object { $_.ProcessId }"
    )
    env = {**os.environ, "BL_PROFILE": str(profile_dir)}
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not look for stale display browsers: %s", exc)
        return []
    own = os.getpid()
    return [int(line) for line in out.split() if line.isdigit() and int(line) != own]


def kill_pid_tree(pid: int) -> None:
    """Force-kill ``pid`` and its children (Windows)."""
    subprocess.run(
        ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


class ProjectorBrowser:
    """Owns the kiosk browser process so it can be closed again."""

    def __init__(
        self,
        *,
        find: Callable[[], Browser | None] = find_browser,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        open_default: Callable[[str], object] = webbrowser.open,
        profile_dir: Path | None = None,
        find_stale: Callable[[Path], list[int]] = find_profile_processes,
        kill_pid: Callable[[int], None] = kill_pid_tree,
    ) -> None:
        self._find_stale = find_stale
        self._kill_pid = kill_pid
        self._find = find
        self._popen = popen
        self._open_default = open_default
        self._profile_dir = profile_dir
        self._proc: subprocess.Popen[bytes] | None = None

    def open(self, port: int, monitor: Monitor | None) -> str | None:
        """Open the display on ``monitor``.

        Raises ``ProjectorError("no_monitor")`` if ``monitor`` is None (never falls back
        to the operator's screen). Returns "browser_fallback" if only the default browser
        could be used, else None.
        """
        if monitor is None:
            raise ProjectorError("no_monitor")
        self.close()
        url = display_url(port)
        browser = self._find()
        if browser is None:
            logger.warning("No Edge/Chrome found; opening the display in the default browser")
            self._open_default(url)
            return "browser_fallback"
        profile = self._profile_dir or default_profile_dir()
        try:
            profile.mkdir(parents=True, exist_ok=True)
            for pid in self._find_stale(profile):
                logger.info("Closing stale display browser process %s", pid)
                self._kill_pid(pid)
            extra: dict[str, int] = {}
            if sys.platform == "win32":
                extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            self._proc = self._popen(
                build_command(browser, url, monitor, profile),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **extra,
            )
        except OSError as exc:
            raise ProjectorError("launch_failed", str(exc)) from exc
        return None

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            kill_tree(proc)
