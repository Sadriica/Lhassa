"""
Repeated-think detector — the $0 sense that spots automatable patterns (§2.8).

The self-improvement pipeline (quarantine -> gates -> versioning) has always had
a missing FIRST step: nothing ever noticed that the brain was judging (spending
tokens) on essentially the same thing over and over. This module is that sense.

Detection is a PURE SQL aggregation over the episodes table (the append-only
grande): group recent episodes by (despertar_por, accion), count occurrences and
sum cost. A pattern that repeats often AND cost real money is a strong signal a
deterministic action could replace the judgment entirely. This never calls an
LLM and never writes anything — see :mod:`lhassa.actionpack._selfimprove` for the
action that turns a finding into a parked approval + notification.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from lhassa._vendor import aiosqlite
from lhassa.config import Config
from lhassa.core import db as _db

# Default lookback window and repeat count, overridable per call or via
# Config.repetition_threshold.
DEFAULT_DAYS = 7
DEFAULT_THRESHOLD = 5


def pattern_key(despertar_por: str, accion: str) -> str:
    """Normalized grouping key for one repeated-think pattern."""
    return f"{(despertar_por or '').strip()}::{(accion or '').strip()}"


async def detect_repetition(
    instance_id: str,
    *,
    days: int = DEFAULT_DAYS,
    threshold: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Find (despertar_por, accion) patterns repeated >= ``threshold`` times.

    Pure SQL aggregation over the last ``days`` of episodes for ``instance_id`` —
    no LLM, no cost, never raises destructively. Only patterns that both repeat
    at least ``threshold`` times AND have spent real money (``total_cost_usd >
    0``) are returned — a pattern that already costs nothing is not worth
    automating further. Sorted by repeat count descending.

    Each item: ``{"pattern", "despertar_por", "accion", "count", "total_cost_usd"}``.
    """
    threshold = (
        threshold if threshold is not None else int(Config.repetition_threshold)
    )
    if threshold < 1:
        threshold = DEFAULT_THRESHOLD
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """SELECT despertar_por, accion,
                          COUNT(*) AS n,
                          SUM(COALESCE(costo_usd, 0)) AS cost
                   FROM episodes
                   WHERE instance_id = ? AND creado_en >= ?
                   GROUP BY despertar_por, accion
                   HAVING COUNT(*) >= ? AND SUM(COALESCE(costo_usd, 0)) > 0
                   ORDER BY n DESC""",
                (instance_id, cutoff, threshold),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
    except Exception:
        # A missing/corrupt table degrades to "nothing detected" — this is a
        # cheap sense, not a critical path; it must never crash a caller.
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        despertar_por = row.get("despertar_por") or ""
        accion = row.get("accion") or ""
        out.append(
            {
                "pattern": pattern_key(despertar_por, accion),
                "despertar_por": despertar_por,
                "accion": accion,
                "count": int(row.get("n") or 0),
                "total_cost_usd": float(row.get("cost") or 0.0),
            }
        )
    return out
