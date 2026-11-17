"""Windows launcher operations, moved from the launcher support modules."""

import ctypes
import logging
import ntpath
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from . import Browser, Monitor, RunFn, ShellExecuteFn
from .chromium import build_command

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
    if sys.platform == "win32":  # the flag exists only there (and mypy checks other platforms)
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


FIREWALL_RULE_NAME = "BibleLive"
SW_HIDE = 0
_SYSTEM32 = ntpath.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")


def firewall_applies() -> bool:
    return True


def firewall_programs(executable: str) -> list[str]:
    """python.exe and pythonw.exe beside ``executable`` (whichever exist).

    The launcher starts the server and the audio pipeline with this same interpreter, so these
    are the programs that listen on the network.
    """
    folder = os.path.dirname(os.path.abspath(executable))
    paths = [os.path.join(folder, name) for name in ("python.exe", "pythonw.exe")]
    return [path for path in paths if os.path.isfile(path)]


def firewall_add_script(programs: Sequence[str], system32: str | None = None) -> str:
    """netsh commands that add one inbound allow rule per program, Private profile only.

    Public networks stay blocked. The commands are joined with "&" (not "&&") so that a rule
    which fails does not stop the next one.
    """
    netsh = ntpath.join(system32 or _SYSTEM32, "netsh.exe")
    return " & ".join(
        f'"{netsh}" advfirewall firewall add rule name={FIREWALL_RULE_NAME} dir=in action=allow '
        f'profile=private enable=yes program="{program}"'
        for program in programs
    )


def firewall_rule_exists(run: RunFn = subprocess.run, system32: str | None = None) -> bool:
    """True if the BibleLive rule exists. Showing a rule needs no administrator rights.

    netsh exits with 1 and "No rules match" when there is no such rule; a rule that exists
    prints its name. A netsh that cannot run counts as no rule.
    """
    command = [
        ntpath.join(system32 or _SYSTEM32, "netsh.exe"),
        "advfirewall",
        "firewall",
        "show",
        "rule",
    ]
    try:
        result = run(
            [*command, f"name={FIREWALL_RULE_NAME}"],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no console flash
        )
    except OSError:
        return False
    return result.returncode == 0 and FIREWALL_RULE_NAME.encode() in result.stdout


def _shell_execute_runas(file: str, parameters: str) -> int:
    """ShellExecuteW with the "runas" verb, which shows the UAC prompt. Above 32 means started."""
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined,unused-ignore]
    return int(shell32.ShellExecuteW(None, "runas", file, parameters, None, SW_HIDE))


def add_firewall_rule(
    programs: Sequence[str],
    shell_execute: ShellExecuteFn | None = None,
    system32: str | None = None,
) -> bool:
    """Add the rules in one elevated cmd.exe, so there is one administrator prompt.

    True if the elevated command started, that is the prompt was accepted. ShellExecute does
    not wait for the command to finish, so the caller looks for the rule afterwards.
    """
    system32 = system32 or _SYSTEM32
    script = firewall_add_script(programs, system32)
    run_elevated = shell_execute or _shell_execute_runas
    return run_elevated(ntpath.join(system32, "cmd.exe"), f'/c "{script}"') > 32


def app_data_dir() -> Path:
    """Per-user data directory: ``%APPDATA%\\BibleLive`` (``~/.biblelive`` elsewhere)."""
    appdata = os.environ.get("APPDATA")
    if sys.platform == "win32" and appdata:
        return Path(appdata) / "BibleLive"
    return Path.home() / ".biblelive"


def models_dir() -> Path:
    """Where speech models are cached (``HF_HOME``): ``%LOCALAPPDATA%\\BibleLive\\models``.

    Machine-local rather than roaming: the models are ~1.6 GB. ``~/.biblelive/models`` elsewhere.
    """
    local = os.environ.get("LOCALAPPDATA")
    if sys.platform == "win32" and local:
        return Path(local) / "BibleLive" / "models"
    return Path.home() / ".biblelive" / "models"


def kill_tree(proc: subprocess.Popen[bytes], *, grace: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    subprocess.run(
        ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=grace)


def child_process_options() -> dict[str, int]:
    extra: dict[str, int] = {}
    # Console Ctrl+C must reach only the launcher (which kills the trees), and no
    # console window may flash when started from pythonw.
    if sys.platform == "win32":  # the flags exist only there (and mypy checks other platforms)
        extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    return extra


def log_dir(data_dir: Path) -> Path:
    return data_dir / "logs"
