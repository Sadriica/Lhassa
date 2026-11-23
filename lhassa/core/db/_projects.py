# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agent runs/steps, artifacts, projects, project brains, and bench runs.

Split out of the former monolithic ``lhassa/core/db.py``; part of the
``lhassa.core.db`` facade package (see ``lhassa/core/db/__init__.py``).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from lhassa._vendor import aiosqlite

from ._conn import _pkg

async def record_run(row: dict) -> str:
    """Insert or replace one durable run record, scoped to ``instance_id``."""
    run_id = row.get("id") or _pkg()._new_id("run")
    instance_id = row["instance_id"]
    raw = row.get("raw")
    if not isinstance(raw, str):
        raw = json.dumps(raw if raw is not None else row, ensure_ascii=False)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO runs(
                   id, instance_id, kind, entity_id, parent_run_id, status, trigger,
                   summary, started_at, finished_at, duration_ms, cost_usd,
                   input_tokens, output_tokens, raw)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                instance_id,
                row.get("kind") or "",
                row.get("entity_id"),
                row.get("parent_run_id"),
                row.get("status") or "completed",
                row.get("trigger"),
                row.get("summary"),
                row.get("started_at") or _pkg()._now(),
                row.get("finished_at"),
                int(row.get("duration_ms") or 0),
                float(row.get("cost_usd") or 0.0),
                int(row.get("input_tokens") or 0),
                int(row.get("output_tokens") or 0),
                raw,
            ),
        )
        await db.commit()
    return run_id


async def record_run_step(row: dict) -> str:
    """Insert one durable run step row, scoped to ``instance_id``."""
    step_id = row.get("id") or _pkg()._new_id("step")
    raw = row.get("raw")
    if not isinstance(raw, str):
        raw = json.dumps(raw if raw is not None else row, ensure_ascii=False)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO run_steps(
                   id, instance_id, run_id, idx, kind, entity_id, status,
                   started_at, finished_at, duration_ms, cost_usd,
                   input_summary, output_summary, error, raw)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                step_id,
                row["instance_id"],
                row["run_id"],
                int(row.get("idx") or 0),
                row.get("kind") or "",
                row.get("entity_id"),
                row.get("status") or "completed",
                row.get("started_at"),
                row.get("finished_at"),
                int(row.get("duration_ms") or 0),
                float(row.get("cost_usd") or 0.0),
                row.get("input_summary"),
                row.get("output_summary"),
                row.get("error"),
                raw,
            ),
        )
        await db.commit()
    return step_id


async def list_runs(
    instance_id: str,
    *,
    kind: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """Return durable runs, newest first, optionally filtered by kind/entity."""
    sql = "SELECT * FROM runs WHERE instance_id = ?"
    args: list[Any] = [instance_id]
    if kind is not None:
        sql += " AND kind = ?"
        args.append(kind)
    if entity_id is not None:
        sql += " AND entity_id = ?"
        args.append(entity_id)
    sql += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
    args.append(limit)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["raw"] = _pkg()._loads(row.get("raw"), {})
    return rows


async def list_run_steps(instance_id: str, run_id: str) -> list[dict]:
    """Return the steps for one run, ordered by execution index."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM run_steps
               WHERE instance_id = ? AND run_id = ?
               ORDER BY idx, rowid""",
            (instance_id, run_id),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["raw"] = _pkg()._loads(row.get("raw"), {})
    return rows


# ── facts (append-only; superseding = a new row) ─────────────────────────────────

async def insert_artifact(row: dict) -> str:
    """Insert one artifact row into the grande. Returns the artifact id.

    ``row`` carries every column (id, instance_id, kind, paths, metadata,
    origin_department, episode_id, creado_en); ``paths``/``metadata`` may be a
    pre-encoded JSON string or a raw list/dict (encoded here). Append-only: this
    never overwrites an existing artifact. Scoped to ``instance_id``.
    """
    paths = row.get("paths")
    paths_json = paths if isinstance(paths, str) else _pkg()._dumps(list(paths or []))
    metadata = row.get("metadata")
    metadata_json = (
        metadata if isinstance(metadata, str) else json.dumps(metadata or {}, ensure_ascii=False)
    )
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO artifacts(
                   id, instance_id, kind, paths, metadata,
                   origin_department, episode_id, creado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["instance_id"],
                row["kind"],
                paths_json,
                metadata_json,
                row.get("origin_department", ""),
                row.get("episode_id"),
                row["creado_en"],
            ),
        )
        await db.commit()
    return row["id"]


async def get_artifact(instance_id: str, artifact_id: str) -> Optional[dict]:
    """Return one artifact row (paths/metadata decoded), or ``None`` if absent.

    Scoped to ``instance_id`` (isolation from day one).
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM artifacts WHERE instance_id = ? AND id = ?",
            (instance_id, artifact_id),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["paths"] = _pkg()._loads(out.get("paths"), [])
    out["metadata"] = _pkg()._loads(out.get("metadata"), {})
    return out


async def list_artifacts(instance_id: str, *, limit: int = 50) -> list[dict]:
    """Return artifacts for ``instance_id`` (newest first, cap ``limit``).

    ``paths``/``metadata`` decoded. Always scoped to ``instance_id``.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM artifacts
               WHERE instance_id = ?
               ORDER BY creado_en DESC, rowid DESC
               LIMIT ?""",
            (instance_id, limit),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["paths"] = _pkg()._loads(row.get("paths"), [])
        row["metadata"] = _pkg()._loads(row.get("metadata"), {})
    return rows


# ── idea assessment funnel (projection + append-only events) ──────────────────

async def insert_project(row: dict) -> str:
    """Insert one project row. Returns the project id. Scoped to ``instance_id``."""
    metadata = row.get("metadata")
    metadata_json = (
        metadata if isinstance(metadata, str)
        else json.dumps(metadata or {}, ensure_ascii=False)
    )
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO projects(
                   id, instance_id, name, kind, path, status,
                   metadata, owner_department, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["instance_id"],
                row["name"],
                row.get("kind") or "generic",
                row.get("path"),
                row.get("status") or "active",
                metadata_json,
                row.get("owner_department"),
                row["created_at"],
                row["updated_at"],
            ),
        )
        await db.commit()
    return row["id"]


async def get_project_row(instance_id: str, project_id: str) -> "Optional[dict]":
    """One project row (metadata decoded), or ``None``."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM projects WHERE instance_id = ? AND id = ?",
            (instance_id, project_id),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["metadata"] = _pkg()._loads(out.get("metadata"), {})
    return out


async def list_project_rows(
    instance_id: str, *, status: "Optional[str]" = "active", kind: "Optional[str]" = None
) -> "list[dict]":
    """Project rows for ``instance_id`` (newest first). ``status=None`` lists all."""
    sql = "SELECT * FROM projects WHERE instance_id = ?"
    params: list = [instance_id]
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY created_at DESC"
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["metadata"] = _pkg()._loads(row.get("metadata"), {})
    return rows


async def update_project_row(
    instance_id: str, project_id: str, fields: dict
) -> bool:
    """Update allowed mutable columns on one project. Returns whether a row changed."""
    allowed = {"name", "kind", "path", "status", "owner_department", "metadata", "updated_at"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "metadata" and not isinstance(value, str):
            value = json.dumps(value or {}, ensure_ascii=False)
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return False
    params.extend([instance_id, project_id])
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE instance_id = ? AND id = ?",
            params,
        )
        await db.commit()
        return bool(cur.rowcount)


# ── project_brains (standing-brain attachments. Wave 1) ─────────────────────


async def insert_project_brains(row: dict) -> None:
    """Record one template attachment. INSERT OR IGNORE; the row is the idempotency."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO project_brains(
                   project_id, instance_id, template_id, status, instantiated_at)
               VALUES(?, ?, ?, ?, ?)""",
            (
                row["project_id"],
                row["instance_id"],
                row["template_id"],
                row.get("status") or "attached",
                row["instantiated_at"],
            ),
        )
        await db.commit()


async def list_project_brains(
    instance_id: str, project_id: "Optional[str]" = None
) -> "list[dict]":
    """Attachment rows for one project (or the whole instance)."""
    sql = "SELECT * FROM project_brains WHERE instance_id = ?"
    params: list = [instance_id]
    if project_id is not None:
        sql += " AND project_id = ?"
        params.append(project_id)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(row) for row in await cur.fetchall()]


# ── knowledge graph (kg_entities / kg_edges. Wave 3, dumb storage) ──────────


async def insert_bench_run(row: dict) -> str:
    """Record one scored bench run. Returns its id."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO bench_runs(
                   id, instance_id, fixture_id, subject, passed, metric,
                   detail, cost_usd, duration_ms, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["instance_id"],
                row["fixture_id"],
                row["subject"],
                1 if row.get("passed") else 0,
                row.get("metric"),
                (row.get("detail") or "")[:500],
                float(row.get("cost_usd") or 0.0),
                row.get("duration_ms"),
                row["created_at"],
            ),
        )
        await db.commit()
    return row["id"]


async def list_bench_runs(
    instance_id: str, *, fixture_id: "Optional[str]" = None,
    subject: "Optional[str]" = None, limit: int = 200,
) -> "list[dict]":
    """Bench rows (newest first), optionally filtered."""
    sql = "SELECT * FROM bench_runs WHERE instance_id = ?"
    params: list = [instance_id]
    if fixture_id is not None:
        sql += " AND fixture_id = ?"
        params.append(fixture_id)
    if subject is not None:
        sql += " AND subject = ?"
        params.append(subject)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(row) for row in await cur.fetchall()]


# ── kg entity merge (resolution; repoint edges, keep provenance) ────────────


