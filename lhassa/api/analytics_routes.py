"""Execution analytics routes for the dashboard.

Episodes are narrative audit. These endpoints read the durable ``runs`` and
``run_steps`` tables to answer operational questions: what ran, how often, how long
it took, what failed, and which brains/flows are active.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query
from loguru import logger

import lhassa.config as lhassa_config
from lhassa._vendor import aiosqlite
from lhassa.core import db

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _since(range_: str) -> Optional[str]:
    value = (range_ or "7d").strip().lower()
    if value in {"all", "∞"}:
        return None
    now = datetime.now(timezone.utc)
    if value.endswith("h"):
        hours = int(value[:-1] or "24")
        return (now - timedelta(hours=hours)).isoformat()
    if value.endswith("d"):
        days = int(value[:-1] or "7")
        return (now - timedelta(days=days)).isoformat()
    return (now - timedelta(days=7)).isoformat()


def _empty_stats(entity_id: str, *, label: str = "") -> dict:
    return {
        "entity_id": entity_id,
        "label": label or entity_id,
        "runs": 0,
        "completed": 0,
        "failed": 0,
        "stopped": 0,
        "needs_approval": 0,
        "success_rate": None,
        "avg_duration_ms": 0,
        "total_duration_ms": 0,
        "total_cost_usd": 0.0,
        "last_run_at": None,
        "last_status": None,
    }


def _add_run(stats: dict, row: dict) -> None:
    status = row.get("status") or "completed"
    stats["runs"] += 1
    if status == "completed":
        stats["completed"] += 1
    elif status == "stopped":
        stats["stopped"] += 1
    elif status == "needs_approval":
        stats["needs_approval"] += 1
    else:
        stats["failed"] += 1
    duration = int(row.get("duration_ms") or 0)
    stats["total_duration_ms"] += duration
    stats["total_cost_usd"] = round(
        float(stats["total_cost_usd"] or 0.0) + float(row.get("cost_usd") or 0.0),
        6,
    )
    started = row.get("started_at")
    if started and (not stats["last_run_at"] or started > stats["last_run_at"]):
        stats["last_run_at"] = started
        stats["last_status"] = status


def _finish_stats(stats: dict) -> dict:
    runs = int(stats.get("runs") or 0)
    if runs:
        stats["avg_duration_ms"] = int(stats["total_duration_ms"] / runs)
        stats["success_rate"] = round(stats["completed"] / runs, 4)
    return stats


async def _rows(sql: str, args: tuple) -> list[dict]:
    try:
        await db.init_db()
        async with aiosqlite.connect(db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(sql, args) as cur:
                return [dict(row) for row in await cur.fetchall()]
    except Exception as exc:
        logger.debug("analytics: could not read execution rows: {}", exc)
        return []


async def _flow_labels() -> dict[str, str]:
    try:
        from lhassa.flows.store import FlowStore

        return {
            f.id: (f.name or f.id)
            for f in FlowStore(lhassa_config.Config()).list_flows()
        }
    except Exception:
        return {}


async def _brain_labels() -> dict[str, str]:
    try:
        from lhassa.org import topology as topo_mod

        top = topo_mod.load_topology(lhassa_config.Config())
        return {
            n.id: f"{n.id}{f' · {n.department}' if n.department else ''}"
            for n in top.nodes()
        } | {
            n.department: f"{n.department} · {n.id}"
            for n in top.nodes()
            if n.department
        }
    except Exception:
        return {}


@router.get("/summary")
async def analytics_summary(range: str = Query(default="7d")) -> dict:
    """High-level run counters for Overview/Reports."""
    since = _since(range)
    instance_id = lhassa_config.Config.instance_id
    where = "WHERE instance_id = ?"
    args: list = [instance_id]
    if since:
        where += " AND started_at >= ?"
        args.append(since)
    runs = await _rows(
        f"""SELECT kind, status, duration_ms, cost_usd, started_at
            FROM runs {where}
            ORDER BY started_at DESC""",
        tuple(args),
    )
    by_kind: dict[str, dict] = {}
    total = _empty_stats("all", label="All runs")
    for row in runs:
        kind = row.get("kind") or "unknown"
        by_kind.setdefault(kind, _empty_stats(kind, label=kind))
        _add_run(by_kind[kind], row)
        _add_run(total, row)
    return {
        "range": range,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": _finish_stats(total),
        "by_kind": {k: _finish_stats(v) for k, v in sorted(by_kind.items())},
    }


@router.get("/flows")
async def analytics_flows(range: str = Query(default="30d")) -> dict:
    """Aggregated flow run performance."""
    since = _since(range)
    instance_id = lhassa_config.Config.instance_id
    where = "WHERE instance_id = ? AND kind = 'flow'"
    args: list = [instance_id]
    if since:
        where += " AND started_at >= ?"
        args.append(since)
    rows = await _rows(
        f"""SELECT entity_id, status, duration_ms, cost_usd, started_at
            FROM runs {where}
            ORDER BY started_at DESC""",
        tuple(args),
    )
    labels = await _flow_labels()
    out: dict[str, dict] = {
        flow_id: _empty_stats(flow_id, label=label)
        for flow_id, label in labels.items()
    }
    for row in rows:
        entity_id = row.get("entity_id") or "unknown"
        out.setdefault(entity_id, _empty_stats(entity_id, label=labels.get(entity_id, entity_id)))
        _add_run(out[entity_id], row)
    items = [_finish_stats(v) for v in out.values()]
    items.sort(key=lambda x: (x["runs"], x["last_run_at"] or ""), reverse=True)
    return {"range": range, "items": items}


@router.get("/brains")
async def analytics_brains(range: str = Query(default="30d")) -> dict:
    """Aggregated task/brain step performance."""
    since = _since(range)
    instance_id = lhassa_config.Config.instance_id
    where = "WHERE s.instance_id = ? AND s.kind = 'task'"
    args: list = [instance_id]
    if since:
        where += " AND COALESCE(s.started_at, r.started_at) >= ?"
        args.append(since)
    rows = await _rows(
        f"""SELECT s.entity_id, s.status, s.duration_ms, s.cost_usd,
                   COALESCE(s.started_at, r.started_at) AS started_at
            FROM run_steps s
            LEFT JOIN runs r ON r.id = s.run_id AND r.instance_id = s.instance_id
            {where}
            ORDER BY started_at DESC""",
        tuple(args),
    )
    labels = await _brain_labels()
    out: dict[str, dict] = {}
    for row in rows:
        entity_id = row.get("entity_id") or "unknown"
        out.setdefault(entity_id, _empty_stats(entity_id, label=labels.get(entity_id, entity_id)))
        _add_run(out[entity_id], row)
    items = [_finish_stats(v) for v in out.values()]
    items.sort(key=lambda x: (x["runs"], x["last_run_at"] or ""), reverse=True)
    return {"range": range, "items": items}


@router.get("/runs")
async def analytics_runs(
    kind: Optional[str] = Query(default=None),
    entity_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """Recent durable runs, optionally filtered by kind/entity."""
    try:
        runs = await db.list_runs(
            lhassa_config.Config.instance_id,
            kind=kind,
            entity_id=entity_id,
            limit=limit,
        )
    except Exception as exc:
        logger.debug("analytics: could not list runs: {}", exc)
        runs = []
    return {"items": runs}


@router.get("/feedback")
async def analytics_feedback(range: str = Query(default="7d")) -> dict:
    """The $0 feedback signal — is Lhassa landing? (see lhassa.core.feedback).

    Inferred from behavior already in the episode log (re-ask/correction/
    silence-after/acted-on) — never asks the owner to rate anything, never
    calls a model. Folded into this router (rather than a new ``*_routes``
    module) per the feature's own read-surface note.
    """
    value = (range or "7d").strip().lower()
    days = 7
    if value.endswith("d") and value[:-1].isdigit():
        days = int(value[:-1])
    elif value.endswith("h") and value[:-1].isdigit():
        days = max(1, int(int(value[:-1]) / 24))
    try:
        from lhassa.core import feedback as _feedback

        return await _feedback.summary(lhassa_config.Config.instance_id, days=days)
    except Exception as exc:
        logger.debug("analytics: feedback summary failed: {}", exc)
        return {
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "total": 0,
            "net_score": 0.0,
            "range_days": days,
        }


@router.get("/runs/{run_id}")
async def analytics_run_detail(run_id: str) -> dict:
    """One run with its persisted steps."""
    instance_id = lhassa_config.Config.instance_id
    try:
        runs = await db.list_runs(instance_id, limit=500)
        run = next((r for r in runs if r.get("id") == run_id), None)
        steps = await db.list_run_steps(instance_id, run_id) if run else []
    except Exception as exc:
        logger.debug("analytics: could not read run detail {}: {}", run_id, exc)
        run = None
        steps = []
    return {
        "run": run,
        "steps": steps,
    }
