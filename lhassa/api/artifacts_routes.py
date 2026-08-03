# SPDX-License-Identifier: AGPL-3.0-or-later
"""Artifacts routes — the read surface over the delivery chain's registry
(§org: "Lhassa is the sender of record").

A thin, read-only shell over :mod:`lhassa.core.artifacts`: every work product a
department produces and delivers UP the chain (:mod:`lhassa.org.deliver`) lands
one append-only row here — a CV's ``.tex``+``.pdf``, a report, any file the owner
ends up receiving. This router only reads; nothing here writes an artifact (that
happens exclusively through ``deliver()``).

Routes (all under ``/api/artifacts``)::

    GET /api/artifacts       → the newest artifacts (cap ``limit``, default 50)
    GET /api/artifacts/{id}  → one artifact                        → 200 | 404

Mounted by the integrator with ``app.include_router(artifacts_routes.router)``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from lhassa.config import Config
from lhassa.core import artifacts, db

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("")
async def list_artifacts(limit: int = 50) -> list[dict]:
    """The newest registered artifacts for this instance (newest first)."""
    await db.init_db()
    return await artifacts.list_artifacts(Config.instance_id, limit=limit)


@router.get("/{artifact_id}")
async def get_artifact(artifact_id: str) -> dict:
    """One artifact by id (404 if missing)."""
    await db.init_db()
    row = await artifacts.get(Config.instance_id, artifact_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"artifact {artifact_id!r} not found")
    return row
