"""Quarantine-room routes — the self-written tools and their gate state (§2.9-(2)).

GET lists the tools table (the quarantine-room data: name/domain/estado/metrics/
usage). POST graduates a candidate: "aprobación = merge" — it routes a
tool_graduation approval through the approval queue and merges the candidate
branch into main via the local-git versioning layer. Offline; no GitHub token.

Graduation is gated: a tool may only be graduated after it has PASSED the four
gates (:mod:`lhassa.agents.gates` — segura/correcta/mejor recorded via
``estado="graduada"`` on the tools row). This endpoint refuses (409) a tool whose
row is missing or not yet ``graduada`` — it must never be the thing that grants
trust; :func:`lhassa.agents.gates.promote` (or :func:`lhassa.agents.author.propose_tool`)
is. This is what stood BETWEEN "approve" and "merge" being the same click as
"skip the gates entirely".

Mounted by the integrator with ``app.include_router(tools_routes.router)``.
"""

from fastapi import APIRouter, HTTPException

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

    Body (all optional): ``{branch?, approval_id?, by_actor?}``. REFUSES (409) a
    tool that has not already passed the four gates (its ``tools`` row must show
    ``estado="graduada"`` — see :func:`lhassa.agents.gates.promote`); this
    endpoint is the human-approval + merge step, never a way to skip the gates.
    On approve, routes a ``tool_graduation`` approval through the approval queue
    and merges the candidate branch into main. Returns the merge commit (empty
    when git is unavailable — the approval still resolves).
    """
    from lhassa.agents import versioning

    payload = payload or {}
    branch = payload.get("branch")
    approval_id = payload.get("approval_id")
    by_actor = (payload.get("by_actor") or "duenio").strip() or "duenio"

    await db.init_db()

    row = await db.get_tool(Config.instance_id, nombre)
    estado = row.get("estado") if row else None
    if estado != "graduada":
        raise HTTPException(
            status_code=409,
            detail=(
                f"tool {nombre!r} has not passed the gates yet "
                f"(estado={estado!r}); run the four gates "
                "(lhassa.agents.gates.promote / lhassa.agents.author.propose_tool) "
                "before graduating"
            ),
        )

    merge_commit = await versioning.approve(
        nombre,
        branch=branch,
        instance_id=Config.instance_id,
        approval_id=approval_id,
        by_actor=by_actor,
    )
    await db.upsert_tool(Config.instance_id, nombre, estado="graduada")
    return {"ok": True, "tool": nombre, "estado": "graduada", "merge_commit": merge_commit}
