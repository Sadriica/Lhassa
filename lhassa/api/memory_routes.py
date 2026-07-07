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


@router.get("/api/memory/graph")
async def get_memory_graph(q: str = ""):
    """The knowledge graph's flagship query — ``what_do_i_know(q)``, $0, no model."""
    from lhassa.memory import graph

    return await graph.what_do_i_know(None, q)


@router.get("/api/memory/entities")
async def get_memory_entities():
    """The distinct entities the brain tracks — a map of what it knows about."""
    from lhassa.memory import graph

    return {"entities": await graph.entities(None)}
