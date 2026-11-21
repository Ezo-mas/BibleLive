"""Network access: the operator token, this computer's address on the local network, and the
Windows Firewall rule that lets other devices reach it.

Off by default. With "allow other devices on this network" on, the server listens on all
interfaces and every operator action needs the token (``process.build_plan`` sets that up).
The display pages stay view-only and need no token.
"""

import io
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from urllib.parse import quote

import segno

from core.lan import lan_ip as lan_ip

from . import platform as platform_api
from .platform import RunFn, ShellExecuteFn
from .platform.windows import FIREWALL_RULE_NAME as FIREWALL_RULE_NAME
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
# The administrator prompt can wait for the user; the rule is looked for this long.
FIREWALL_WAIT_S = 120.0

# The launcher adds the rule when the user asks for it (one administrator prompt) and the
# installer removes it on uninstall. Other devices can reach the server only on Private networks.


def firewall_applies(platform: str = sys.platform) -> bool:
    """Only Windows has this rule; elsewhere the launcher shows no firewall row."""
    return platform_api.current(platform).firewall_applies()


def firewall_programs(executable: str) -> list[str]:
    return platform_api.current().firewall_programs(executable)


def firewall_add_script(programs: Sequence[str], system32: str | None = None) -> str:
    return platform_api.current().firewall_add_script(programs, system32)


def firewall_rule_exists(run: RunFn = subprocess.run, system32: str | None = None) -> bool:
    return platform_api.current().firewall_rule_exists(run, system32)


def add_firewall_rule(
    programs: Sequence[str],
    shell_execute: ShellExecuteFn | None = None,
    system32: str | None = None,
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
    shell_execute: ShellExecuteFn | None = None,
    run: RunFn = subprocess.run,
    wait_s: float = FIREWALL_WAIT_S,
) -> bool:
    """Add the rules (one administrator prompt) and look for them. True if the rule is there.

    A declined prompt is not waited for: the rule is checked once and the answer is no.
    """
    started = add_firewall_rule(programs, shell_execute)
    return wait_until(lambda: firewall_rule_exists(run), timeout=wait_s if started else 0.0)
