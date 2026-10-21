# SPDX-License-Identifier: AGPL-3.0-or-later
"""Overview routes; one defensive aggregate for the dashboard home.

Serves the core read-model :func:`lhassa.views.overview.build_overview`, the same
aggregate ``lhassa status`` renders in-process. Each section is gathered
independently and defensively, so the response is always complete and never 500s.
LLM-free.

Mounted by the integrator with ``app.include_router(overview_routes.router)``.
"""

from fastapi import APIRouter

from lhassa.views.overview import build_overview

router = APIRouter()


@router.get("/api/overview")
async def get_overview():
    """One defensive aggregate for the dashboard home / ``lhassa status``.

    Sections: ``brains``, ``skills``, ``approvals`` (with the pending list so the
    owner can approve from here), ``cost`` (today + budget + kill-switch),
    ``health`` (worst level + ok), ``activity`` (recent episodes) and ``tools``
    (count by estado). Each is gathered independently; any failure lands in
    ``errors`` instead of taking the response down. Never 500s; LLM-free.
    """
    return await build_overview()
