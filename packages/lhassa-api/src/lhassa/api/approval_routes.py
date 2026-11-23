# SPDX-License-Identifier: AGPL-3.0-or-later
"""Approval frontier routes; the HITL queue (§2.6, §2.9-(1)).

The dashboard's first panel is "la cola de aprobaciones". GET returns the pending
queue; POST resolves one approval and feeds the autonomy knob (a repeatedly approved
human action earns auto-approval; a denial resets its streak). HITL is per-flow: the
threshold lives per action by risk, never one global knob.

Mounted by the integrator with ``app.include_router(approval_routes.router)``.
"""

from fastapi import APIRouter, HTTPException, Request
import json
import re
from loguru import logger

import lhassa.config as lhassa_config
from lhassa.core import db
from lhassa.flows.runtime import RuntimeConflict
from lhassa.security import approval

router = APIRouter()


def _trusted_api_approver(request: Request) -> approval.TrustedApprover:
    """Derive owner identity from middleware, or the same loopback-only policy.

    The fallback keeps this router self-contained when mounted on a minimal app;
    it is fail-closed for non-loopback clients and whenever an API token is
    configured (only the server middleware can validate that secret).
    """
    source = str(getattr(request.state, "lhassa_auth_source", "") or "")
    host = request.client.host if request.client else None
    local_hosts = {"127.0.0.1", "::1", "localhost", "testclient"}
    if not source and not getattr(lhassa_config.Config, "api_token", "") and host in local_hosts:
        source = "loopback"
    if not source:
        raise approval.UntrustedApproverError("authenticated API identity required")
    return approval.TrustedApprover(actor_id="owner", source=source)


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
async def resolve_approval_endpoint(approval_id: str, payload: dict, request: Request):
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
    # ``by_actor`` remains accepted for wire compatibility and optional UI labels,
    # but it is not an authorization claim. The middleware-authenticated API
    # channel is the authoritative resolver identity.
    by_actor = raw_actor.strip() or "duenio"
    try:
        trusted = _trusted_api_approver(request)
    except approval.UntrustedApproverError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    await db.init_db()
    try:
        result = await approval.resolve(
            lhassa_config.Config.instance_id,
            approval_id,
            approved=approved,
            by_actor=by_actor,
            approver=trusted,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"approval {approval_id!r} not found") from exc
    except (RuntimeConflict, approval.ApprovalResolutionConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except approval.SelfApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except approval.ApprovalAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    from lhassa.views import waybar_state

    waybar_state.request_refresh()  # the status-bar widget follows the queue
    return {
        "ok": True,
        "approval_id": approval_id,
        "approved": approved,
        "resolved_by": trusted.actor_id,
        **(result or {}),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Briefs: enriched decision queue (categoria, origen, resumen, recomendacion).
# Append-only addition; the original GET /api/approvals is untouched.
# ─────────────────────────────────────────────────────────────────────────────

_CATEGORIAS = {
    "retire_capability": ("Self-improvement", "Utility tribunal",   "low"),
    "mint_action":       ("Self-improvement", "Repetition agent",   "medium"),
    "mint_script":       ("Self-improvement", "Repetition agent",   "medium"),
    "mint_memory":       ("Memory",           "Repetition agent",   "low"),
    "mint_schedule":     ("Scheduler",        "Repetition agent",   "low"),
    "seed_cache":        ("Self-improvement", "Repetition agent",   "low"),
    "tool_graduation":   ("Tools",            "Security",           "medium"),
    "flow_gate":         ("Flows",            "Coordinator",        "medium"),
    "org_task":          ("Organization",     "Org",                "high"),
}


def _categorize(tipo: str) -> dict:
    categoria, origen, riesgo = _CATEGORIAS.get(tipo, ("General", "Lhassa", "medium"))
    return {"categoria": categoria, "origen": origen, "riesgo": riesgo}


async def _ensure_brief_column() -> None:
    import aiosqlite
    from lhassa.core.db._conn import _pkg
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        async with db.execute("PRAGMA table_info(approvals)") as cur:
            cols = [r[1] for r in await cur.fetchall()]
        if "brief" not in cols:
            await db.execute("ALTER TABLE approvals ADD COLUMN brief TEXT")
            await db.commit()


def _no_em_dash(text: str) -> str:
    """The owner's rule: no em dashes anywhere, including model-written text."""
    return re.sub(r"\s*[\u2014\u2013]\s*", ": ", text or "").strip()


async def _store_brief(approval_id: str, brief: dict) -> None:
    import aiosqlite
    from lhassa.core.db._conn import _pkg
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            "UPDATE approvals SET brief = ? WHERE id = ?",
            (json.dumps(brief, ensure_ascii=False), approval_id),
        )
        await db.commit()


async def _generate_briefs(rows: list[dict]) -> None:
    """Batch-generate missing briefs with one cheap-model call; cached by gateway."""
    from lhassa.config import Config
    from lhassa.llm.gateway import gateway
    items = [
        {
            "id": r["id"],
            "tipo": r.get("tipo"),
            "title": (r.get("payload") or {}).get("title", ""),
            "rationale": (r.get("payload") or {}).get("rationale", ""),
            "detail": approval.describe(r).get("detail", ""),
        }
        for r in rows
    ]
    prompt = (
        "You are Lhassa, the owner's personal assistant. For EACH item in the "
        "decision queue write a short summary (1-2 sentences) and a clear "
        "recommendation: approve or reject, with the reason. Write in ENGLISH, "
        "direct and warm tone, no fluff. Never use em dashes; use ':' or ',' "
        "instead. Return ONLY JSON: a list of objects "
        '{"id", "summary", "recommendation"} where "recommendation" starts with '
        '"Approve" or "Reject".\n\nItems:\n'
        + json.dumps(items, ensure_ascii=False)
    )
    result = await gateway.complete(
        [{"role": "user", "content": prompt}],
        model=Config().model_cheap,
        max_tokens=2500,
        temperature=0.2,
    )
    _raw = (result.text or "").strip()
    _raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", _raw, flags=re.S)
    _m = re.search(r"\[[\s\S]*\]", _raw)
    data = json.loads(_m.group(0) if _m else _raw)
    if not isinstance(data, list):
        return
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            await _store_brief(str(item["id"]), {
                "summary": _no_em_dash(str(item.get("summary", ""))),
                "recommendation": _no_em_dash(str(item.get("recommendation", ""))),
            })


@router.get("/api/approvals/briefs")
async def get_approval_briefs():
    """Pending queue enriched with categoria, origen, riesgo, resumen y recomendacion."""
    import json as _json
    await db.init_db()
    await _ensure_brief_column()
    rows = await approval.pending(lhassa_config.Config.instance_id)
    missing = [r for r in rows if not (r.get("brief") or "").strip()]
    if missing:
        try:
            await _generate_briefs(missing)
            rows = await approval.pending(lhassa_config.Config.instance_id)
        except Exception as exc:  # never fail the queue over a brief
            logger.warning(f"brief generation failed: {exc}")
    out = []
    for row in rows:
        cat = _categorize(row.get("tipo") or "?")
        brief = {}
        try:
            brief = _json.loads(row.get("brief") or "{}")
        except Exception:
            brief = {}
        out.append({
            **row,
            "categoria": brief.get("categoria") or cat["categoria"],
            "origen": brief.get("origen") or cat["origen"],
            "riesgo": brief.get("riesgo") or cat["riesgo"],
            "resumen": brief.get("summary") or brief.get("resumen", ""),
            "recomendacion": brief.get("recommendation") or brief.get("recomendacion", ""),
            "summary": approval.describe(row),
        })
    # Newest decision first: the queue reads like an inbox, not a backlog.
    out.sort(key=lambda r: str(r.get("creado_en") or ""), reverse=True)
    return out
