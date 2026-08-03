# SPDX-License-Identifier: AGPL-3.0-or-later
"""DEPRECATED — the legacy 30s reminder poller. Retired; kept as a no-op shim.

Reminders now go through the same do-and-sleep backbone as everything else:
``tools/handlers/schedule.py`` mints a ``send_reminder`` task (``core/db.py``'s
``tasks`` table, via ``core.dispatch.mint_task``) and the heartbeat's watchlist
(``core/watchlist.py:_from_pending_tasks``) picks it up once its ``due_at`` has
passed — zero-token, no separate polling loop. See ``doc/scheduler.md``.

``run_scheduler`` is kept only so any process still importing/calling it
(older deployments, stray references) degrades to a harmless no-op instead of
crashing. It is no longer started by ``main.py`` or ``docker/run_api.py``.
"""

from __future__ import annotations

from loguru import logger


async def run_scheduler() -> None:
    """No-op. Logs one deprecation line and returns immediately.

    The legacy ``memory/store.py`` ``scheduled_tasks`` table is migrated,
    once, into the ``tasks`` table by
    ``tools.handlers.schedule.migrate_legacy_reminders`` — see that function
    and ``doc/scheduler.md`` for the one-shot migration this replaces.
    """
    logger.info(
        "lhassa.scheduler.run_scheduler() is deprecated and does nothing — "
        "reminders now run through core/db.py's tasks table + the heartbeat "
        "(see doc/scheduler.md)."
    )
