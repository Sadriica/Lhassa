"""Memory routes — the dashboard's read view of the grande (facts + sessions).

Thin read-only wrappers over the memory store: the typed facts and the recent
voice-session summaries the dashboard's memory surface renders.

Mounted by the integrator with ``app.include_router(memory_routes.router)``.
"""

from fastapi import APIRouter

from lhassa.memory.store import load_facts, load_recent_sessions

router = APIRouter()


@router.get("/api/memory/facts")
async def get_facts():
    return await load_facts()


@router.get("/api/memory/sessions")
async def get_sessions():
    return await load_recent_sessions(20)
