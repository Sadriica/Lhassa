"""Quarantine-room routes — the self-written tools and their gate state (§2.9-(2)).

GET lists the tools table (the quarantine-room data: name/domain/estado/metrics/
usage). POST graduates a candidate: "aprobación = merge" — it routes a
tool_graduation approval through the approval queue and merges the candidate
branch into main via the local-git versioning layer. Offline; no GitHub token.

Mounted by the integrator with ``app.include_router(tools_routes.router)``.
"""

from fastapi import APIRouter

from lhassa.config import Config
from lhassa.core import db

router = APIRouter()


@router.get("/api/tools")
async def get_tools():
    """The quarantine room: every tool with its estado + gate/metric/usage info."""
    await db.init_db()
    rows = await db.list_tools(Config.instance_id)
    return [
        {
            "id": r.get("id"),
            "nombre": r.get("nombre"),
            "dominio": r.get("dominio"),
            "estado": r.get("estado"),
            "metrica_baseline": r.get("metrica_baseline"),
            "metrica_actual": r.get("metrica_actual"),
            "usos": r.get("usos"),
            "fallos": r.get("fallos"),
            "creado_en": r.get("creado_en"),
        }
        for r in rows
    ]


@router.post("/api/tools/{nombre}/graduate")
async def graduate_tool(nombre: str, payload: dict | None = None):
    """Approve a tool's graduation: aprobación = merge.

    Body (all optional): ``{branch?, approval_id?, by_actor?}``. Routes a
    ``tool_graduation`` approval through the approval queue and, on approve, merges
    the candidate branch into main. Marks the tool ``graduada``. Returns the merge
    commit (empty when git is unavailable — the approval still resolves).
    """
    from lhassa.agents import versioning

    payload = payload or {}
    branch = payload.get("branch")
    approval_id = payload.get("approval_id")
    by_actor = (payload.get("by_actor") or "duenio").strip() or "duenio"

    await db.init_db()
    merge_commit = await versioning.approve(
        nombre,
        branch=branch,
        instance_id=Config.instance_id,
        approval_id=approval_id,
        by_actor=by_actor,
    )
    await db.upsert_tool(Config.instance_id, nombre, estado="graduada")
    return {"ok": True, "tool": nombre, "estado": "graduada", "merge_commit": merge_commit}
