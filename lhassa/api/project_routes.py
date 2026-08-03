# SPDX-License-Identifier: AGPL-3.0-or-later
"""Projects routes — the read/list surface over the project entity
(§core.projects: "a named, TYPED unit of the owner's work").

A thin shell over :mod:`lhassa.core.projects` + the durable tables that already
reference a project through the namespaced-string convention
(:func:`lhassa.core.projects.entity_tag`) — no new columns, no new tables.

Routes (all under ``/api/projects``)::

    GET /api/projects                  → active projects (``?all=1`` includes archived, ``?kind=``)
    GET /api/projects/{id}             → one project                              → 200 | 404
    GET /api/projects/{id}/runs        → runs whose entity_id == entity_tag(id)   → 200 | 404
    GET /api/projects/{id}/artifacts   → artifacts whose metadata.project_id == id → 200 | 404
    GET /api/projects/{id}/episodes    → episodes mentioning the project id (FTS5) → 200 | 404

Mounted by the integrator with ``app.include_router(project_routes.router)``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from lhassa.config import Config
from lhassa.core import db, projects

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def _get_or_404(project_id: str) -> dict:
    row = await projects.get_project(Config.instance_id, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return row


@router.get("")
async def list_projects(
    all: bool = Query(default=False, description="Include archived projects."),
    kind: str | None = Query(default=None),
) -> list[dict]:
    """Projects for this instance, newest first."""
    return await projects.list_projects(
        Config.instance_id, status=None if all else "active", kind=kind
    )


@router.get("/{project_id}")
async def get_project(project_id: str) -> dict:
    """One project by id (404 if missing)."""
    return await _get_or_404(project_id)


@router.get("/{project_id}/runs")
async def get_project_runs(
    project_id: str, limit: int = Query(default=200, ge=1, le=500)
) -> list[dict]:
    """Durable runs scoped to this project via ``entity_id == project:<id>``."""
    await _get_or_404(project_id)
    await db.init_db()
    return await db.list_runs(
        Config.instance_id, entity_id=projects.entity_tag(project_id), limit=limit
    )


@router.get("/{project_id}/artifacts")
async def get_project_artifacts(
    project_id: str, limit: int = Query(default=200, ge=1, le=500)
) -> list[dict]:
    """Artifacts whose ``metadata.project_id`` matches, filtered in Python over a
    bounded recent window (no schema change — ``metadata`` is opaque JSON)."""
    await _get_or_404(project_id)
    await db.init_db()
    recent = await db.list_artifacts(Config.instance_id, limit=limit)
    return [row for row in recent if (row.get("metadata") or {}).get("project_id") == project_id]


@router.get("/{project_id}/episodes")
async def get_project_episodes(
    project_id: str, limit: int = Query(default=20, ge=1, le=100)
) -> list[dict]:
    """Recent episodes mentioning this project id, BM25-ranked (FTS5), bounded."""
    await _get_or_404(project_id)
    await db.init_db()
    return await db.search_episodes(Config.instance_id, project_id, n=limit)
