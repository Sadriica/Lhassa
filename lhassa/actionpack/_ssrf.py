"""SSRF guard shared by web_fetch and ping_url.

Refuses URLs that point at loopback, private, or link-local address ranges so
an action cannot be used as a Server-Side Request Forgery vector against the
local network or the Lhassa host itself.

Blocked ranges (per the Phase-3 spec):
  IPv4: 127/8, 10/8, 172.16/12, 192.168/16, 169.254/16
  IPv6: ::1 (loopback), fc00::/7 (ULA)

Also refuses the hostname literal "localhost" (before DNS resolution) and any
scheme other than http/https.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Networks that are always blocked regardless of DNS resolution.
_BLOCKED_NETS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("0.0.0.0/8"),       # "this network" (0.0.0.0 → localhost on Linux)
    ipaddress.ip_network("127.0.0.0/8"),    # loopback
    ipaddress.ip_network("10.0.0.0/8"),      # private (RFC1918)
    ipaddress.ip_network("172.16.0.0/12"),   # private (RFC1918)
    ipaddress.ip_network("192.168.0.0/16"),  # private (RFC1918)
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),         # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),        # IPv6 ULA (private)
    ipaddress.ip_network("fe80::/10"),       # IPv6 link-local
]


def _is_blocked_addr(addr_str: str) -> bool:
    """True if ``addr_str`` falls in a blocked network range."""
    try:
        ip = ipaddress.ip_address(addr_str)
    except ValueError:
        return True  # unparseable → block by default
    return any(ip in net for net in _BLOCKED_NETS)


def ssrf_check(url: str) -> str | None:
    """Return an error string if ``url`` is unsafe, else ``None``.

    Checks (in order):
    1. Scheme must be http or https.
    2. Host must not be the literal string 'localhost'.
    3. All resolved IP addresses must lie outside blocked ranges.

    Returns ``None`` when the URL is safe to fetch.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return f"invalid URL: {exc}"

    if parsed.scheme not in ("http", "https"):
        return f"scheme {parsed.scheme!r} not allowed (only http/https)"

    host = (parsed.hostname or "").strip()
    if not host:
        return "missing host in URL"

    if host.lower() == "localhost":
        return "host 'localhost' is not allowed (SSRF guard)"

    # Resolve the host and check every returned address.
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return f"could not resolve host {host!r}: {exc}"
    except OSError as exc:
        return f"host resolution error for {host!r}: {exc}"

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        addr = sockaddr[0]
        if _is_blocked_addr(addr):
            return (
                f"host {host!r} resolves to a private/loopback/link-local "
                f"address ({addr!r}) — blocked by SSRF guard"
            )

    return None
