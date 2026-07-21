"""Approval frontier routes — the HITL queue (§2.6, §2.9-(1)).

The dashboard's first panel is "la cola de aprobaciones". GET returns the pending
queue; POST resolves one approval and feeds the autonomy knob (a repeatedly approved
human action earns auto-approval; a denial resets its streak). HITL is per-flow: the
threshold lives per action by risk, never one global knob.

Mounted by the integrator with ``app.include_router(approval_routes.router)``.
"""

from fastapi import APIRouter, HTTPException

import lhassa.config as lhassa_config
from lhassa.core import db
from lhassa.flows.runtime import RuntimeConflict
from lhassa.security import approval

router = APIRouter()


@router.get("/api/approvals")
async def get_approvals():
    """The pending approval queue for this instance (oldest first).

    Each row carries a ``summary`` (``{title, detail}`` from
    :func:`lhassa.security.approval.describe`) so every UI shows what is being
    approved, not just ids.
    """
    await db.init_db()
    rows = await approval.pending(lhassa_config.Config.instance_id)
    return [{**row, "summary": approval.describe(row)} for row in rows]


@router.post("/api/approvals/{approval_id}")
async def resolve_approval_endpoint(approval_id: str, payload: dict):
    """Resolve a pending approval. Body: ``{approved: bool, by_actor?: str}``.

    Marks the approval aprobada/rechazada and feeds the autonomy knob so a
    repeatedly approved human action earns auto-approval (denials reset the streak).
    """
    if not isinstance(payload, dict) or "approved" not in payload:
        raise HTTPException(status_code=400, detail="'approved' (bool) is required.")
    approved = payload["approved"]
    if not isinstance(approved, bool):
        raise HTTPException(status_code=400, detail="'approved' must be a boolean.")
    raw_actor = payload.get("by_actor", "duenio")
    if not isinstance(raw_actor, str):
        raise HTTPException(status_code=400, detail="'by_actor' must be a string.")
    by_actor = raw_actor.strip() or "duenio"

    await db.init_db()
    try:
        result = await approval.resolve(
            lhassa_config.Config.instance_id,
            approval_id,
            approved=approved,
            by_actor=by_actor,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"approval {approval_id!r} not found") from exc
    except (RuntimeConflict, approval.ApprovalResolutionConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "approval_id": approval_id,
        "approved": approved,
        **(result or {}),
    }
