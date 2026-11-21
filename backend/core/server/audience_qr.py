"""The audience QR code: a code on the displays that opens the display page on a phone.

The server builds the address from where it listens, never from a request, so whoever can
turn the code on cannot point the audience at another site.
"""

from collections.abc import Callable

import segno

from ..lan import lan_ip
from .models import AudienceQrData

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
_WILDCARD_HOSTS = {"", "0.0.0.0"}

HIDDEN = AudienceQrData(url=None, svg=None)


def audience_url(host: str, port: int, get_lan_ip: Callable[[], str | None] = lan_ip) -> str | None:
    """The display page's address for other devices, or None if they cannot reach it.

    A server on loopback only (the launcher without network access) has no such address.
    On all interfaces it is this computer's local-network address; on one interface, that one.
    """
    host = host.strip().lower()
    if host in _LOOPBACK_HOSTS or host.startswith("127.") or ":" in host:
        return None  # IPv6 binds are not used by the launcher; no code for them
    ip = get_lan_ip() if host in _WILDCARD_HOSTS else host
    if not ip:
        return None
    return f"http://{ip}:{port}/display"


def audience_qr(url: str) -> AudienceQrData:
    """``url`` and its QR code as an SVG data URI: black on white, quiet zone included."""
    code = segno.make(url, error="m", micro=False)
    return AudienceQrData(url=url, svg=code.svg_data_uri(scale=1, border=4))
