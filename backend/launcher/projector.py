"""Open the audience display full-screen on the projector monitor (Edge/Chrome app window)."""

import logging
import subprocess
import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path

from . import platform
from .monitors import Monitor
from .platform import Browser as Browser
from .process import kill_tree
from .settings import app_data_dir

logger = logging.getLogger(__name__)


class ProjectorError(Exception):
    """``code`` is "no_monitor" or "launch_failed" (the GUI translates it)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def display_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/display"


def operator_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/operator"


def default_profile_dir() -> Path:
    return app_data_dir() / "display-browser"


def find_browser(env: Mapping[str, str] | None = None) -> Browser | None:
    return platform.current().find_browser(env)


def build_command(browser: Browser, url: str, monitor: Monitor, profile_dir: Path) -> list[str]:
    return platform.current().build_command(browser, url, monitor, profile_dir)


def find_profile_processes(profile_dir: Path) -> list[int]:
    return platform.current().find_profile_processes(profile_dir)


def kill_pid_tree(pid: int) -> None:
    platform.current().kill_pid_tree(pid)


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
            self._proc = platform.current().open_projector(
                browser, url, monitor, profile, popen=self._popen
            )
        except OSError as exc:
            raise ProjectorError("launch_failed", str(exc)) from exc
        return None

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        kill_tree(proc)
        # Edge and Chrome often hand the window to another browser process that is not a
        # child of the one started, so killing that tree can leave the display open. Every
        # process on our private profile is ours (the user's normal browser never is).
        for pid in self._find_stale(self._profile_dir or default_profile_dir()):
            self._kill_pid(pid)
