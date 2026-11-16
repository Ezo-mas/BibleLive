"""Windows launcher operations, moved from the launcher support modules."""

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from . import Browser, Monitor

logger = logging.getLogger("launcher.projector")


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
    """Pure: the command line placing a full-screen app window on ``monitor``.

    A full-screen app window, not kiosk mode: it looks the same on the projector (no
    browser bars), but F11 leaves full screen and Alt+F4 closes it, which kiosk mode
    blocks, leaving the operator no way to move or close the window.
    """
    return [
        browser.path,
        f"--app={url}",
        "--start-fullscreen",
        "--no-first-run",
        "--no-default-browser-check",
        # The private profile must stay empty: Edge otherwise signs it into the Windows
        # account on first launch and syncs that account's extensions into it, which
        # opens an extra window of extension-install pages next to the display.
        "--disable-sync",
        "--disable-extensions",
        "--disable-features=msImplicitSignin",
        f"--window-position={monitor.x},{monitor.y}",
        f"--user-data-dir={profile_dir}",
    ]


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


def open_projector(
    browser: Browser,
    url: str,
    monitor: Monitor,
    profile_dir: Path,
    *,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> subprocess.Popen[bytes]:
    extra: dict[str, int] = {}
    extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return popen(
        build_command(browser, url, monitor, profile_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **extra,
    )


def enumerate_monitors() -> list[Monitor]:
    """All attached monitors (primary first); empty off Windows or if the query fails."""
    if sys.platform != "win32":
        return []
    try:
        return _enumerate_windows()
    except (AttributeError, OSError):
        return []


def _enumerate_windows() -> list[Monitor]:
    # sys.platform (not os.name) so mypy skips this body on Linux, where
    # typeshed has no ctypes.WINFUNCTYPE.
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [  # noqa: RUF012 (ctypes convention)
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HANDLE,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )
    user32 = ctypes.windll.user32  # type: ignore[attr-defined,unused-ignore]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFOEXW)]
    found: list[Monitor] = []

    def _cb(hmon: int, _hdc: int, _rect: object, _lparam: int) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append(
                Monitor(
                    device=info.szDevice,
                    x=r.left,
                    y=r.top,
                    width=r.right - r.left,
                    height=r.bottom - r.top,
                    primary=bool(info.dwFlags & 1),  # MONITORINFOF_PRIMARY
                )
            )
        return True

    user32.EnumDisplayMonitors(None, None, callback_type(_cb), 0)
    return sorted(found, key=lambda m: (not m.primary, m.device))
