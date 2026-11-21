"""This computer's IPv4 address on the local network.

Shared by the launcher (the addresses it shows) and the server (the audience QR code).
"""

import socket


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
