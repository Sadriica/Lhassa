# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack routes; which capability packs this process has loaded.

``GET /api/packs`` lists the :data:`lhassa.extensions.PACKS` manifests (name,
summary), so the dashboard can hide the sections whose pack is absent (Research,
Calendar, Postulaciones). Read-only, LLM-free, never raises.

Mounted by the integrator with ``app.include_router(packs_routes.router)``.
"""

from fastapi import APIRouter

from lhassa.extensions import PACKS

router = APIRouter()


@router.get("/api/packs")
async def list_packs() -> dict:
    """The loaded packs, sorted by name: ``{"packs": [{"name", "summary"}]}``."""
    packs = sorted(PACKS.items().values(), key=lambda p: p.name)
    return {"packs": [{"name": p.name, "summary": p.summary} for p in packs]}
