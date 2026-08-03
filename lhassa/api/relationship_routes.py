# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed org RELATIONSHIPS API — the dashboard backend for the overlay graph.

A self-contained :class:`fastapi.APIRouter` (``router``) the integrator mounts with
``app.include_router(relationship_routes.router)``. It exposes the overlay graph
(:mod:`lhassa.org.relationships`) — the typed edges (reads / notifies / reviews /
collaborates) that sit ON TOP of the command tree — so the dashboard canvas can read
and draw them and the owner can add/remove edges.

DEFENSIVE: a bad body / invalid op returns a clear 4xx (400 for a bad kind / id /
self-edge, 404 for a missing brain), NEVER a 500, and the underlying ops validate
BEFORE they persist so the relationships file is never corrupted. Purely additive.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from lhassa.org import relationships as rel

router = APIRouter()


def _rel_4xx(exc: Exception) -> HTTPException:
    """Map a relationship error to a 4xx (404 for a missing brain, else 400)."""
    detail = str(exc)
    if "no brain" in detail:
        return HTTPException(status_code=404, detail=detail)
    return HTTPException(status_code=400, detail=detail)


@router.get("/api/org/relationships")
async def get_relationships():
    """The whole overlay graph (``describe``): typed edges + the kind vocabulary."""
    return rel.describe()


@router.get("/api/org/relationships/kinds")
async def get_relationship_kinds():
    """The closed kind set with one-line descriptions (the UI legend)."""
    return {
        "kinds": [
            {
                "kind": k,
                "description": rel.KIND_DESCRIPTIONS.get(k, ""),
                "symmetric": k in rel.SYMMETRIC_KINDS,
            }
            for k in rel.RELATION_KINDS
        ]
    }


@router.post("/api/org/relationships")
async def add_relationship(payload: dict):
    """Add a typed edge. Body: ``{from, to, kind, note?, scope?}``.

    Returns the updated overlay (``describe``). A bad kind / self-edge returns 400, a
    missing brain 404; the file is never partially written.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="relationship payload must be a JSON object.")
    from_id = (payload.get("from") or payload.get("from_id") or "").strip()
    to_id = (payload.get("to") or payload.get("to_id") or "").strip()
    kind = (payload.get("kind") or "").strip()
    if not from_id or not to_id:
        raise HTTPException(status_code=400, detail="'from' and 'to' are required.")
    if not kind:
        raise HTTPException(status_code=400, detail="'kind' is required.")
    try:
        rel.add(
            from_id,
            to_id,
            kind,
            note=str(payload.get("note") or ""),
            scope=str(payload.get("scope") or ""),
        )
    except ValueError as exc:  # RelationshipError → 4xx
        raise _rel_4xx(exc) from exc
    return rel.describe()


@router.delete("/api/org/relationships")
async def delete_relationship(
    payload: dict | None = None,
    from_id: str | None = Query(default=None, alias="from"),
    to_id: str | None = Query(default=None, alias="to"),
    kind: str | None = Query(default=None),
):
    """Remove a typed edge. Body ``{from, to, kind}`` OR the same query params.

    Idempotent — removing an absent edge still returns the (unchanged) overlay.
    Returns the updated overlay (``describe``).
    """
    body = payload if isinstance(payload, dict) else {}
    f = (body.get("from") or body.get("from_id") or from_id or "").strip()
    t = (body.get("to") or body.get("to_id") or to_id or "").strip()
    k = (body.get("kind") or kind or "").strip()
    if not f or not t:
        raise HTTPException(status_code=400, detail="'from' and 'to' are required.")
    if not k:
        raise HTTPException(status_code=400, detail="'kind' is required.")
    try:
        rel.remove(f, t, k)
    except ValueError as exc:
        raise _rel_4xx(exc) from exc
    return rel.describe()
