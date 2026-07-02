"""Fleet routes — the multi-machine fleet view (Phase-6, read-only).

``/api/fleet`` derives the known machines from existing reads: check-in episodes
(``despertar_por`` = ``checkin:<machine>``) carry the last-seen time and
``desired_state`` carries the reconciliation target per machine. Read-only and
instance-scoped.

``Config`` is read through the ``lhassa.config`` MODULE (``lhassa_config.Config``)
rather than a bound name, so a config reload (some endpoint tests reload it) is
picked up live — the same seam ``runtime_routes`` uses.

Mounted by the integrator with ``app.include_router(fleet_routes.router)``.
"""

from fastapi import APIRouter

import lhassa.config as lhassa_config
from lhassa.core import db

router = APIRouter()


@router.get("/api/fleet")
async def get_fleet():
    """Known machines with their last check-in and desired_state.

    Derived from existing reads: check-in episodes (``despertar_por`` =
    ``checkin:<machine>``) carry the last-seen time; ``desired_state`` carries the
    reconciliation target per machine. A small inline query unions the two."""
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
