# SPDX-License-Identifier: AGPL-3.0-or-later
"""Projects — the first-class workspace entity Lhassa works ON.

A project is a named, TYPED unit of the owner's work: a code repo, a research
effort, an ops surface. ``kind`` is an open string (``code`` / ``research`` /
``ops`` / ``generic`` — matching the house style of ``dominio``), because
project-type POLICY keys off it: the standing-brains reflex maps
``kind → org template`` so a ``code`` project gets its reviewer brains attached
without being asked (see core/policy.py, Wave 1).

Scoping convention (no schema sprawl): existing tables reference a project
through the namespaced string ``project:<id>`` in ``runs.entity_id`` and a
``project_id`` key in ``artifacts.metadata`` — promoted to real columns only if
that ever proves insufficient. :func:`entity_tag` is the ONE place the
convention is spelled.

Everything here is $0, deterministic, and never raises past its seam: a bad id
degrades to ``None``, a bad path to no-match.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from lhassa.core import db

# Known-but-open kinds; anything else is accepted verbatim (open vocabulary,
# like ``dominio``). Listed for surfaces that want to offer a picker.
SUGGESTED_KINDS = ("code", "research", "ops", "personal", "generic")


def entity_tag(project_id: str) -> str:
    """The ``runs.entity_id`` namespacing for a project — the one spelling."""
    return f"project:{project_id}"


def _now() -> str:
    return db._now()


async def create_project(
    instance_id: str,
    name: str,
    *,
    kind: str = "generic",
    path: Optional[str] = None,
    metadata: Optional[dict] = None,
    owner_department: str = "",
) -> dict:
    """Create one project and return its row. ``path`` is expanded + resolved.

    Raises ``ValueError`` on an empty name or a duplicate active project with
    the same name — creation is an explicit owner act (CLI/chat/approval), so a
    loud refusal beats a silent duplicate.
    """
    clean = (name or "").strip()
    if not clean:
        raise ValueError("project name must not be empty")
    await db.init_db()
    existing = await db.list_project_rows(instance_id, status="active")
    if any(p["name"] == clean for p in existing):
        raise ValueError(f"an active project named {clean!r} already exists")

    resolved_path = None
    if path:
        resolved_path = str(Path(os.path.expanduser(str(path))).resolve())

    now = _now()
    row = {
        "id": db._new_id("prj"),
        "instance_id": instance_id,
        "name": clean,
        "kind": (kind or "generic").strip().lower() or "generic",
        "path": resolved_path,
        "status": "active",
        "metadata": metadata or {},
        "owner_department": owner_department or "",
        "created_at": now,
        "updated_at": now,
    }
    await db.insert_project(row)
    return row


async def get_project(instance_id: str, project_id: str) -> Optional[dict]:
    """One project by id, or ``None``. Never raises."""
    try:
        await db.init_db()
        return await db.get_project_row(instance_id, project_id)
    except Exception:
        return None


async def list_projects(
    instance_id: str, *, status: Optional[str] = "active", kind: Optional[str] = None
) -> list[dict]:
    """Projects (newest first); ``status=None`` includes archived. Never raises."""
    try:
        await db.init_db()
        return await db.list_project_rows(instance_id, status=status, kind=kind)
    except Exception:
        return []


async def resolve_project(instance_id: str, ref: str) -> Optional[dict]:
    """Resolve ``ref`` — a project id, exact name, or a filesystem path that
    lives INSIDE a project's ``path`` — to its row. ``None`` when nothing
    matches. Deterministic: id beats name beats path; among path matches the
    DEEPEST root wins (a nested project shadows its parent). Never raises.
    """
    try:
        ref = (ref or "").strip()
        if not ref:
            return None
        await db.init_db()
        rows = await db.list_project_rows(instance_id, status="active")
        for row in rows:
            if row["id"] == ref:
                return row
        for row in rows:
            if row["name"] == ref:
                return row
        try:
            target = Path(os.path.expanduser(ref)).resolve()
        except Exception:
            return None
        best: Optional[dict] = None
        best_depth = -1
        for row in rows:
            root = row.get("path")
            if not root:
                continue
            root_path = Path(root)
            if target == root_path or root_path in target.parents:
                depth = len(root_path.parts)
                if depth > best_depth:
                    best, best_depth = row, depth
        return best
    except Exception:
        return None


async def archive_project(instance_id: str, project_id: str) -> bool:
    """Archive (never delete) a project. Returns whether a row changed."""
    try:
        await db.init_db()
        return await db.update_project_row(
            instance_id, project_id, {"status": "archived", "updated_at": _now()}
        )
    except Exception:
        return False
