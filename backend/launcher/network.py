"""Network access: the operator token and this
computer's address on the local network.

Off by default. With "allow other devices on this network" on, the server listens on all
interfaces and every operator action needs the token (``process.build_plan`` sets that up).
The display pages stay view-only and need no token.
"""

import secrets
import socket
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


