"""Network access: the operator token, this computer's address on the local network, and the
Windows Firewall rule that lets other devices reach it.

Off by default. With "allow other devices on this network" on, the server listens on all
interfaces and every operator action needs the token (``process.build_plan`` sets that up).
The display pages stay view-only and need no token.
"""

import io
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from urllib.parse import quote

import segno

from . import platform as platform_api
from .platform.windows import _SYSTEM32 as _SYSTEM32
from .platform.windows import FIREWALL_RULE_NAME as FIREWALL_RULE_NAME
from .platform.windows import FIREWALL_WAIT_S as FIREWALL_WAIT_S
from .platform.windows import SW_HIDE as SW_HIDE
from .platform.windows import RunFn as RunFn
from .platform.windows import ShellExecuteFn as ShellExecuteFn
from .platform.windows import _shell_execute_runas as _shell_execute_runas
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


# --- QR codes -------------------------------------------------------------------------
# For a phone's camera. A module is QR_MODULE_PX pixels, so a code is about 185-205 px square.
# Micro QR is never used: many phone cameras cannot read it.
QR_MODULE_PX = 5
QR_QUIET_ZONE_MODULES = 4


def qr_png(text: str) -> bytes:
    """``text`` as a QR code in PNG: black on white, quiet zone included. Needs no Pillow."""
    buf = io.BytesIO()
    segno.make(text, error="m", micro=False).save(
        buf, kind="png", scale=QR_MODULE_PX, border=QR_QUIET_ZONE_MODULES
    )
    return buf.getvalue()


# --- Windows Firewall -----------------------------------------------------------------------
# The launcher adds the rule when the user asks for it (one administrator prompt) and the
# installer removes it on uninstall. Other devices can reach the server only on Private networks.


def firewall_applies(platform: str = sys.platform) -> bool:
    """Only Windows has this rule; elsewhere the launcher shows no firewall row."""
    return platform_api.current(platform).firewall_applies()


def firewall_programs(executable: str) -> list[str]:
    return platform_api.current().firewall_programs(executable)


def firewall_add_script(programs: Sequence[str], system32: str = _SYSTEM32) -> str:
    return platform_api.current().firewall_add_script(programs, system32)


def firewall_rule_exists(run: RunFn = subprocess.run, system32: str = _SYSTEM32) -> bool:
    return platform_api.current().firewall_rule_exists(run, system32)


def add_firewall_rule(
    programs: Sequence[str],
    shell_execute: ShellExecuteFn = _shell_execute_runas,
    system32: str = _SYSTEM32,
) -> bool:
    return platform_api.current().add_firewall_rule(programs, shell_execute, system32)


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
