# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``send_digest`` action — Etapa 2's scheduled operative digest.

A thin ``(params) -> str`` wrapper over :func:`lhassa.notify.digest.send_digest` so
the deterministic digest builder fits the actionpack calling convention (every
action is a plain callable, never raises, returns a short outcome string). Minted
as a recurring task by the ``digest`` seed (see :mod:`lhassa.seeds.digest`) — twice
daily, $0, no LLM.
"""

from __future__ import annotations

from lhassa.notify.digest import send_digest as _send_digest


async def send_digest(params: dict) -> str:
    """Build the operative digest and fan it out over the configured channels.

    params: (none required — ``config`` defaults to the process ``Config()``).
    Never raises.
    """
    try:
        return await _send_digest(params or {})
    except Exception as exc:
        return f"send_digest: error: {exc}"
