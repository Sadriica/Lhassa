# SPDX-License-Identifier: AGPL-3.0-or-later
"""Episode log (append-only) + FTS5 episodic recall.

Split out of the former monolithic ``lhassa/core/db.py``; part of the
``lhassa.core.db`` facade package (see ``lhassa/core/db/__init__.py``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from lhassa._vendor import aiosqlite

from ._conn import _pkg

async def append_episode(ep: Any) -> str:
    """
    Append an Episode to the grande. Serializes ``memoria_cargada`` into its
    column and stores a full JSON copy of every field in ``raw``. Returns the id.

    ``ep`` is duck-typed (any object exposing the Episode attributes) so this
    module never imports ``episode`` and stays dependency-light.
    """
    raw = json.dumps(
        {
            "id": ep.id,
            "instance_id": ep.instance_id,
            "despertar_por": ep.despertar_por,
            "memoria_cargada": list(ep.memoria_cargada or []),
            "opciones": list(getattr(ep, "opciones", []) or []),
            "decision": ep.decision,
            "accion": ep.accion,
            "resultado": ep.resultado,
            "dominio": getattr(ep, "dominio", None),
            "costo_usd": ep.costo_usd,
            "creado_en": ep.creado_en,
        },
        ensure_ascii=False,
    )
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO episodes(
                   id, instance_id, creado_en, despertar_por, decision, accion,
                   resultado, dominio, costo_usd, memoria_cargada, raw)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ep.id,
                ep.instance_id,
                ep.creado_en,
                ep.despertar_por,
                ep.decision,
                ep.accion,
                ep.resultado,
                getattr(ep, "dominio", None),
                ep.costo_usd,
                _pkg()._dumps(list(ep.memoria_cargada or [])),
                raw,
            ),
        )
        await db.commit()
    return ep.id


async def load_recent_episodes(instance_id: str, n: int = 20) -> list[dict]:
    """
    Return the ``n`` most recent episodes for ``instance_id``, newest first.
    ``memoria_cargada`` is deserialized back into a list.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM episodes
               WHERE instance_id = ?
               ORDER BY creado_en DESC, rowid DESC
               LIMIT ?""",
            (instance_id, n),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["memoria_cargada"] = _pkg()._loads(row.get("memoria_cargada"), [])
    return rows


async def count_episodes(instance_id: str) -> int:
    """Return the total number of episodes for ``instance_id``."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM episodes WHERE instance_id = ?",
            (instance_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] if row else 0)


async def count_episodes_since(instance_id: str, since: str) -> int:
    """Return the number of episodes for ``instance_id`` at or after ``since``."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM episodes
               WHERE instance_id = ? AND creado_en >= ?""",
            (instance_id, since),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] if row else 0)


async def dominio_activity(instance_id: str, since: Optional[str] = None) -> dict[str, dict]:
    """Per-``dominio`` activity summary: ``{dominio: {"last": iso, "count": n}}``.

    One aggregate query over ``episodes`` (not one per brain) — the last episode
    timestamp and a count of episodes at/after ``since`` (defaults to 7 days back),
    grouped by ``dominio``. Rows with no ``dominio`` are skipped (they don't map to
    a brain). The org screens use this to show WHO WORKED, not just who exists.
    """
    if since is None:
        from datetime import timedelta

        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT dominio,
                      MAX(creado_en) AS last_at,
                      SUM(CASE WHEN creado_en >= ? THEN 1 ELSE 0 END) AS recent_count
               FROM episodes
               WHERE instance_id = ? AND dominio IS NOT NULL AND dominio != ''
               GROUP BY dominio""",
            (since, instance_id),
        ) as cur:
            rows = await cur.fetchall()
    return {
        str(row["dominio"]): {"last": row["last_at"], "count": int(row["recent_count"] or 0)}
        for row in rows
    }


# ── recall (FTS5 full-text search over episodes — Fase 1) ───────────────────────
#
# $0, deterministic full-text search over the episode log — no model call. The
# query is SANITIZED into a safe FTS5 MATCH expression (each token individually
# double-quoted) so arbitrary user input (quotes, parens, AND/OR/NOT, dangling
# operators) can never raise an FTS5 syntax error. Automation-originated episodes
# (``despertar_por`` is ``monitor`` or starts with ``task:``) are DEMOTED (not
# hidden) in ranking so interactive history surfaces first.


def _fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Each whitespace-delimited token is individually double-quoted (FTS5 string
    syntax: a doubled `"` escapes a literal quote), then the tokens are joined
    with an implicit AND. This defuses any FTS5 operator/punctuation the user
    might type (``AND``, ``OR``, ``NOT``, ``(``, ``)``, a bare ``"``) — the
    result is always a valid MATCH argument, never a syntax error.
    """
    tokens = query.split()
    quoted = ['"' + tok.replace('"', '""') + '"' for tok in tokens if tok]
    return " ".join(quoted)


async def recall_available(instance_id: str) -> bool:
    """Whether the FTS5 recall index actually exists and is queryable.

    ``search_episodes`` degrades to ``[]`` on ANY failure — the right contract
    for its machine callers (context layers truncate an empty list and move
    on), but at the one place the answer reaches the OWNER (``/recall``) an
    empty list must not silently mean "the index is broken". This probe lets
    that surface say "recall unavailable" instead of "no matches".
    """
    try:
        async with aiosqlite.connect(_pkg().DB_PATH) as db:
            async with db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='episodes_fts'"
            ) as cur:
                return (await cur.fetchone()) is not None
    except Exception:
        return False


async def search_episodes(instance_id: str, query: str, n: int = 10) -> list[dict]:
    """BM25-ranked full-text search over ``instance_id``'s episode log.

    Returns up to ``n`` dicts: ``id``, ``despertar_por``, ``decision``, ``accion``,
    a ``resultado`` snippet (via FTS5 ``snippet()``), ``creado_en`` and ``rank``.
    Automation episodes are ranked BELOW interactive ones for an equally-good
    match (a CASE-based penalty added to the raw bm25 rank), never hidden — the
    row stays indexed and still wins if it is genuinely the better match. The
    penalty is GRADED by how much outcome the episode actually carries:
    ``monitor``, ``task:*`` and the org TRANSPORT hops (``org-deliver`` /
    ``org-delegate`` — pure movement, no result) take the full 5.0, while
    ``temple-execute`` takes only 2.0 because that hop ran the action and holds
    its real ``resultado``. An empty/blank
    ``query`` returns ``[]`` with no query executed. If the FTS5 index is
    missing (older SQLite build), returns ``[]`` (debug-logged) rather than
    raising — recall degrades to "no results", never a crash.
    """
    query = (query or "").strip()
    if not query:
        return []
    match_expr = _fts_query(query)
    if not match_expr:
        return []

    sql = """
        SELECT e.id, e.despertar_por, e.decision, e.accion, e.creado_en,
               snippet(episodes_fts, 2, '[', ']', '…', 8) AS snippet,
               bm25(episodes_fts) + (CASE
                   WHEN e.despertar_por = 'monitor' THEN 5.0
                   WHEN e.despertar_por LIKE 'task:%' THEN 5.0
                   -- Org TRANSPORT: the hierarchy's own paperwork. These carry
                   -- no outcome, only movement ('deliver:lhassa→owner',
                   -- 'forward:guardian→temple', 'delegate:lhassa→temple→…').
                   -- They are the single largest source of episodes (227 of 309
                   -- in one measured week), so leaving them unpenalised let the
                   -- brain's memory of its own life fill with its own memos.
                   WHEN e.despertar_por = 'org-deliver' THEN 5.0
                   WHEN e.despertar_por = 'org-delegate' THEN 5.0
                   -- Org EXECUTION is penalised more gently: this is the hop
                   -- that actually ran the action and carries the real
                   -- resultado ('curate_skills → 3 stale skills archived'), so
                   -- "what happened with curate_skills?" must still find it.
                   WHEN e.despertar_por = 'temple-execute' THEN 2.0
                   ELSE 0.0
               END) AS rank
        FROM episodes_fts
        JOIN episodes e ON e.rowid = episodes_fts.rowid
        WHERE episodes_fts MATCH ? AND e.instance_id = ?
        ORDER BY rank
        LIMIT ?
    """
    try:
        async with aiosqlite.connect(_pkg().DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, (match_expr, instance_id, n)) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
    except Exception as exc:
        logger.debug(f"db: search_episodes unavailable ({exc}); returning no results")
        return []

    return [
        {
            "id": row["id"],
            "despertar_por": row["despertar_por"],
            "decision": row["decision"],
            "accion": row["accion"],
            "resultado": row["snippet"],
            "creado_en": row["creado_en"],
            "rank": row["rank"],
        }
        for row in rows
    ]


# ── runs / run_steps (durable execution analytics) ─────────────────────────────

