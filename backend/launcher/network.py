"""Network access: the operator token, this computer's address on the local network, and the
Windows Firewall rule that lets other devices reach it.

Off by default. With "allow other devices on this network" on, the server listens on all
interfaces and every operator action needs the token (``process.build_plan`` sets that up).
The display pages stay view-only and need no token.
"""

import ctypes
import ntpath
import os
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from urllib.parse import quote

from .settings import LauncherSettings

# Lowercase letters and digits without i, l, o, 0 and 1: easy to read off a screen and type.
TOKEN_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
TOKEN_LENGTH = 12


def new_token() -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def ensure_network_token(settings: LauncherSettings) -> LauncherSettings:
    """Settings with a token when network mode is on and none is saved yet (else unchanged)."""
    if settings.allow_network and not settings.network_token:
        return replace(settings, network_token=new_token())
    return settings


def operator_url_with_token(port: int, token: str, host: str = "127.0.0.1") -> str:
    """The operator page on ``host`` (this PC by default) with the token in the fragment.

    A fragment is never sent to the server and never written to its logs. The page reads
    it once and removes it from the address bar. The token is percent-encoded, because
    URLSearchParams turns a raw "+" into a space.
    """
    return f"http://{host}:{port}/operator#token={quote(token, safe='')}"


def _route_ip() -> str | None:
    """The address the OS would send outside traffic from. A UDP connect sends no packet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1 (documentation range): never answers
            return str(sock.getsockname()[0])
    except OSError:
        return None


def _host_ips() -> list[str]:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return [str(info[4][0]) for info in infos]


def _usable(ip: str) -> bool:
    return not ip.startswith(("127.", "169.254.")) and ip != "0.0.0.0"


def lan_ip() -> str | None:
    """This computer's IPv4 address on the local network, or None if it has none.

    Asks the OS which interface it would use for outside traffic. If that fails, takes the
    first usable address bound to the host name.
    """
    route = _route_ip()
    if route and _usable(route):
        return route
    return next((ip for ip in _host_ips() if _usable(ip)), None)


# --- Windows Firewall -----------------------------------------------------------------------
# The launcher adds the rule when the user asks for it (one administrator prompt) and the
# installer removes it on uninstall. Other devices can reach the server only on Private networks.

FIREWALL_RULE_NAME = "BibleLive"
# The administrator prompt can wait for the user; the rule is looked for this long.
FIREWALL_WAIT_S = 120.0
SW_HIDE = 0
_SYSTEM32 = ntpath.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")

RunFn = Callable[..., "subprocess.CompletedProcess[bytes]"]
ShellExecuteFn = Callable[[str, str], int]


def firewall_applies(platform: str = sys.platform) -> bool:
    """Only Windows has this rule; elsewhere the launcher shows no firewall row."""
    return platform == "win32"


def firewall_programs(executable: str) -> list[str]:
    """python.exe and pythonw.exe beside ``executable`` (whichever exist).

    The launcher starts the server and the audio pipeline with this same interpreter, so these
    are the programs that listen on the network.
    """
    folder = os.path.dirname(os.path.abspath(executable))
    paths = [os.path.join(folder, name) for name in ("python.exe", "pythonw.exe")]
    return [path for path in paths if os.path.isfile(path)]


def firewall_add_script(programs: Sequence[str], system32: str = _SYSTEM32) -> str:
    """netsh commands that add one inbound allow rule per program, Private profile only.

    Public networks stay blocked. The commands are joined with "&" (not "&&") so that a rule
    which fails does not stop the next one.
    """
    netsh = ntpath.join(system32, "netsh.exe")
    return " & ".join(
        f'"{netsh}" advfirewall firewall add rule name={FIREWALL_RULE_NAME} dir=in action=allow '
        f'profile=private enable=yes program="{program}"'
        for program in programs
    )


def firewall_rule_exists(run: RunFn = subprocess.run, system32: str = _SYSTEM32) -> bool:
    """True if the BibleLive rule exists. Showing a rule needs no administrator rights.

    netsh exits with 1 and "No rules match" when there is no such rule; a rule that exists
    prints its name. A netsh that cannot run counts as no rule.
    """
    command = [ntpath.join(system32, "netsh.exe"), "advfirewall", "firewall", "show", "rule"]
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
    shell_execute: ShellExecuteFn = _shell_execute_runas,
    system32: str = _SYSTEM32,
) -> bool:
    """Add the rules in one elevated cmd.exe, so there is one administrator prompt.

    True if the elevated command started, that is the prompt was accepted. ShellExecute does
    not wait for the command to finish, so the caller looks for the rule afterwards.
    """
    script = firewall_add_script(programs, system32)
    return shell_execute(ntpath.join(system32, "cmd.exe"), f'/c "{script}"') > 32


def wait_until(
    check: Callable[[], bool],
    *,
    timeout: float,
    interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Call ``check`` every ``interval`` seconds until it is true (True) or ``timeout`` passes."""
    deadline = clock() + timeout
    while True:
        if check():
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def allow_through_firewall(
    programs: Sequence[str],
    *,
    shell_execute: ShellExecuteFn = _shell_execute_runas,
    run: RunFn = subprocess.run,
    wait_s: float = FIREWALL_WAIT_S,
) -> bool:
    """Add the rules (one administrator prompt) and look for them. True if the rule is there.

    A declined prompt is not waited for: the rule is checked once and the answer is no.
    """
    started = add_firewall_rule(programs, shell_execute)
    return wait_until(lambda: firewall_rule_exists(run), timeout=wait_s if started else 0.0)
