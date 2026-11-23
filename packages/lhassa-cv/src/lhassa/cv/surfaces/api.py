# SPDX-License-Identifier: AGPL-3.0-or-later
"""Postulaciones (job/internship application) routes for the dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Request
from loguru import logger

from lhassa.core.db._approvals import (
    list_postulaciones,
    update_postulacion_estado,
)

router = APIRouter()


def _resolve_instance_id(request: Request) -> str:
    """Prefer the app-level instance; fall back to whatever the table holds."""
    inst = getattr(request.app.state, "instance_id", None)
    if inst:
        return inst
    try:
        from lhassa.core.db import _conn

        async def _first():
            async with _conn.connect() as db:
                async with db.execute(
                    "SELECT DISTINCT instance_id FROM postulaciones LIMIT 1"
                ) as cur:
                    row = await cur.fetchone()
            return row[0] if row else "inst_personal"

        import asyncio

        return asyncio.run(_first())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"postulaciones: instance fallback failed: {exc}")
        return "inst_personal"


@router.get("/api/postulaciones")
async def get_postulaciones(request: Request, estado: str | None = None):
    """List tracked applications (newest first), optionally filtered by estado."""
    try:
        rows = await list_postulaciones(
            _resolve_instance_id(request), estado=estado
        )
        return {"ok": True, "items": rows, "total": len(rows)}
    except Exception as exc:
        logger.error(f"postulaciones list error: {exc}")
        return {"ok": False, "error": str(exc), "items": [], "total": 0}


@router.patch("/api/postulaciones/{postulacion_id}")
async def update_postulacion(
    request: Request, postulacion_id: str, body: dict
):
    """Update estado/categoria/prioridad/link for one tracked application.

    Accepts a partial body: only the supplied keys change, the rest keep their
    current value. ``actualizado_en`` is bumped by the store, which is what the
    dashboard's "Últ. cambio" column shows after the reload.
    """
    try:
        instance = _resolve_instance_id(request)
        rows = await list_postulaciones(instance)
        current = next((r for r in rows if r["id"] == postulacion_id), None)
        if current is None:
            return {"ok": False, "error": "not found", "id": postulacion_id}

        estado = body.get("estado", current.get("estado"))
        categoria = body.get("categoria", current.get("categoria"))
        prioridad = body.get("prioridad", current.get("prioridad"))
        link = body.get("link", current.get("link"))

        ok = await update_postulacion_estado(
            instance,
            postulacion_id,
            estado,
            categoria=categoria,
            prioridad=prioridad,
            link=link,
        )
        if not ok:
            return {"ok": False, "error": "not found", "id": postulacion_id}
        return {"ok": True, "id": postulacion_id}
    except Exception as exc:
        logger.error(f"postulaciones update error: {exc}")
        return {"ok": False, "error": str(exc), "id": postulacion_id}
