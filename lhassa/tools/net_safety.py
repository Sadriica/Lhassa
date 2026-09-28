# SPDX-License-Identifier: AGPL-3.0-or-later
"""Network-target safety shared by adapter and shell web readers.

Refuses non-HTTP schemes and hosts resolving to loopback, private, unspecified,
or link-local ranges.  Keeping this in ``tools`` lets adapters such as research
custody reuse the policy without importing the outer ``actionpack`` shell.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_NETS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_blocked_addr(addr_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr_str)
    except ValueError:
        return True
    return any(ip in network for network in _BLOCKED_NETS)


def ssrf_check(url: str) -> str | None:
    """Return an error for an unsafe URL, otherwise ``None``."""
    _address, error = resolve_public_url(url)
    return error


def resolve_public_url(url: str) -> tuple[str | None, str | None]:
    """Resolve and validate ``url`` once, returning a public IP to connect to.

    Callers that actually fetch should connect to the returned address while
    preserving the original HTTP Host header and TLS SNI. This closes the DNS
    validation/connection gap in which a rebinding name could resolve public
    here and private inside the HTTP client a moment later.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return None, f"invalid URL: {exc}"
    if parsed.scheme not in ("http", "https"):
        return None, f"scheme {parsed.scheme!r} not allowed (only http/https)"
    host = (parsed.hostname or "").strip()
    if not host:
        return None, "missing host in URL"
    if host.lower() == "localhost":
        return None, "host 'localhost' is not allowed (SSRF guard)"
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return None, f"could not resolve host {host!r}: {exc}"
    except OSError as exc:
        return None, f"host resolution error for {host!r}: {exc}"
    addresses: list[str] = []
    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        addr = sockaddr[0]
        if _is_blocked_addr(addr):
            return None, (
                f"host {host!r} resolves to a private/loopback/link-local "
                f"address ({addr!r}); blocked by SSRF guard"
            )
        if addr not in addresses:
            addresses.append(addr)
    if not addresses:
        return None, f"host {host!r} resolved to no usable addresses"
    return addresses[0], None


__all__ = ["_is_blocked_addr", "resolve_public_url", "ssrf_check"]
