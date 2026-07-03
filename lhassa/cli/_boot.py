"""Boot-time setup for the Lhassa CLI — quiet logging before the heavy imports.

The CLI entry does not go through ``main.py`` (which configures loguru), so loguru's
DEFAULT sink is active: a DEBUG-to-stderr sink that dumps the pipecat banner, the
"Tools registered" line, and per-call gateway DEBUG records straight into the REPL.
:func:`quiet_logging` drops that default sink and keeps only a WARNING+ sink, so the
terminal shows the conversation — not the plumbing. Call it BEFORE importing any heavy
``lhassa`` module (those emit at import time). Override the floor with
``LHASSA_CLI_LOG_LEVEL`` (e.g. ``INFO`` to debug, ``OFF`` to silence entirely).

This module imports only the standard library + loguru, so importing it is cheap and
side-effect-free apart from the sink reconfiguration that callers explicitly request.
"""

from __future__ import annotations

import os
import sys

from loguru import logger

_DONE = False


def quiet_logging() -> None:
    """Silence loguru's default DEBUG sink for a clean CLI; keep WARNING+ on stderr."""
    global _DONE
    if _DONE:
        return
    _DONE = True
    logger.remove()
    level = (os.getenv("LHASSA_CLI_LOG_LEVEL") or "WARNING").upper()
    if level in ("OFF", "NONE", "SILENT"):
        return
    logger.add(sys.stderr, level=level, format="<dim>{level: <7}</dim> {message}")

    # Wire actionpack so all 15 deterministic actions are available for any CLI
    # path that mints or runs tasks. Lazy import so this module stays cheap at
    # import time; the actual registration happens on the first boot() call.
    from lhassa import actionpack  # noqa: PLC0415
    actionpack.register_all()
