# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fleet routes; the multi-machine fleet view (Phase-6, read-only).

``/api/fleet`` serves the core read-model :func:`lhassa.core.fleet.list_fleet`
(check-in episodes for the last-seen time, ``desired_state`` for the target per
machine), the same read the Telegram ``/fleet`` command renders.

Mounted by the integrator with ``app.include_router(fleet_routes.router)``.
"""

from fastapi import APIRouter

from lhassa.core import fleet

router = APIRouter()


@router.get("/api/fleet")
async def get_fleet():
    """Known machines with their last check-in and desired_state."""
    return await fleet.list_fleet()
