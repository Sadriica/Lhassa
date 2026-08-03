# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backward-compatible shell import for the shared network-target guard."""

import socket  # re-exported for historical test/monkeypatch seams

from lhassa.tools.net_safety import _is_blocked_addr, ssrf_check

__all__ = ["_is_blocked_addr", "ssrf_check", "socket"]
