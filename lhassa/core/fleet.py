# SPDX-License-Identifier: AGPL-3.0-or-later
"""The multi-machine fleet read-model (Phase-6, read-only).

Derives the known machines from existing reads: check-in episodes
(``despertar_por`` = ``checkin:<machine>``) carry the last-seen time and
``desired_state`` carries the reconciliation target per machine. Read-only and
instance-scoped; served by ``GET /api/fleet`` (api pack) and the Telegram
``/fleet`` command alike.

``Config`` is read through the ``lhassa.config`` MODULE so a config reload (some
endpoint tests reload it) is picked up live.
"""

from __future__ import annotations

import lhassa.config as lhassa_config
from lhassa.core import db

__all__ = ["list_fleet"]


async def list_fleet() -> list[dict]:
    """Known machines with their last check-in and desired_state, sorted by id.

    A small inline query unions the check-in episodes and the ``desired_state``
    rows; each machine then gets its full desired state attached."""
    from lhassa._vendor import aiosqlite

    await db.init_db()
    inst = lhassa_config.Config.instance_id
    machines: dict[str, dict] = {}
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT despertar_por AS dp, MAX(creado_en) AS last
               FROM episodes
               WHERE instance_id = ? AND despertar_por LIKE 'checkin:%'
               GROUP BY despertar_por""",
            (inst,),
        ) as cur:
            for row in await cur.fetchall():
                mid = str(row["dp"]).split("checkin:", 1)[1]
                machines.setdefault(mid, {})["last_checkin"] = row["last"]
        async with conn.execute(
            "SELECT DISTINCT machine_id FROM desired_state WHERE instance_id = ?",
            (inst,),
        ) as cur:
            for row in await cur.fetchall():
                machines.setdefault(row["machine_id"], {})

    fleet = []
    for mid in sorted(machines):
        ds = await db.get_desired_state(inst, mid)
        fleet.append(
            {
                "machine_id": mid,
                "last_checkin": machines[mid].get("last_checkin"),
                "desired_state": ds,
            }
        )
    return fleet
