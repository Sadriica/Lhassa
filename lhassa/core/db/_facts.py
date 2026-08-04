# SPDX-License-Identifier: AGPL-3.0-or-later
"""Facts, fact embeddings, answer cache, and ingested knowledge chunks.

Split out of the former monolithic ``lhassa/core/db.py``; part of the
``lhassa.core.db`` facade package (see ``lhassa/core/db/__init__.py``).
"""

from __future__ import annotations

from typing import Any, Optional

from lhassa._vendor import aiosqlite
from lhassa.config import Config

from ._conn import _pkg

async def insert_fact(row: dict) -> str:
    """
    Insert one fact row into the grande. ``row`` is a dict carrying every column
    (id, instance_id, tipo, contenido, pinned, tags, procedencia, confianza,
    creado_en, actualizado_en); ``tags`` may be a list (serialized here) or a
    pre-encoded JSON string. Append-only: this never overwrites an existing fact.
    ``actor_id`` (optional) is WHO authored the fact — defaults to
    ``Config.root_admin_id`` (the owner) when absent, so every row always has a
    WHO (Stream 6 Phase A). Returns the fact id.
    """
    tags = row.get("tags")
    tags_json = tags if isinstance(tags, str) else _pkg()._dumps(list(tags or []))
    actor_id = str(row.get("actor_id") or "").strip() or Config.root_admin_id
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO facts(
                   id, instance_id, tipo, contenido, pinned, tags,
                   procedencia, confianza, creado_en, actualizado_en, actor_id)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["instance_id"],
                row["tipo"],
                row["contenido"],
                1 if row.get("pinned") else 0,
                tags_json,
                row.get("procedencia", ""),
                float(row.get("confianza", 1.0)),
                row["creado_en"],
                row["actualizado_en"],
                actor_id,
            ),
        )
        await db.commit()
    return row["id"]


async def query_facts(
    instance_id: str,
    *,
    tipo: Optional[str] = None,
    pinned: Optional[bool] = None,
) -> list[dict]:
    """
    Return fact rows for ``instance_id`` (oldest first), optionally filtered by
    ``tipo`` and/or ``pinned``. ``tags`` is decoded back into a list. Always
    scoped to ``instance_id`` (isolation from day one).
    """
    sql = "SELECT * FROM facts WHERE instance_id = ?"
    args: list[Any] = [instance_id]
    if tipo is not None:
        sql += " AND tipo = ?"
        args.append(tipo)
    if pinned is not None:
        sql += " AND pinned = ?"
        args.append(1 if pinned else 0)
    sql += " ORDER BY creado_en, rowid"
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["tags"] = _pkg()._loads(row.get("tags"), [])
        row["pinned"] = bool(row["pinned"])
    return rows


async def upsert_fact_embedding(
    instance_id: str,
    fact_id: str,
    vector: list,
    *,
    model: str = "",
) -> None:
    """Store (or replace) one fact's embedding, scoped to ``instance_id``.

    ``vector`` is a list of floats, persisted as JSON. One row per
    (instance_id, fact_id); re-indexing a fact overwrites its vector.
    """
    vec = [float(x) for x in (vector or [])]
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO fact_embeddings(
                   fact_id, instance_id, dim, vector, model, creado_en)
               VALUES(?, ?, ?, ?, ?, ?)
               ON CONFLICT(instance_id, fact_id)
               DO UPDATE SET dim = excluded.dim,
                             vector = excluded.vector,
                             model = excluded.model,
                             creado_en = excluded.creado_en""",
            (fact_id, instance_id, len(vec), _pkg()._dumps(vec), model, _pkg()._now()),
        )
        await db.commit()


async def get_fact_embeddings(instance_id: str) -> list[dict]:
    """Return stored fact embeddings for ``instance_id``.

    Each dict has ``fact_id``, the decoded ``vector`` (list of floats) and the
    ``model`` tag. Always scoped to ``instance_id`` (isolation from day one).
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT fact_id, vector, model FROM fact_embeddings WHERE instance_id = ?",
            (instance_id,),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "fact_id": row["fact_id"],
                "vector": _pkg()._loads(row.get("vector"), []),
                "model": row.get("model") or "",
            }
        )
    return out


async def query_facts_by_tags(instance_id: str, tags) -> list[dict]:
    """
    Return fact rows for ``instance_id`` whose tag set intersects ``tags``
    (oldest first). Tag matching is exact membership (NOT semantic). Scoped to
    ``instance_id``.
    """
    wanted = {t for t in (tags or [])}
    if not wanted:
        return []
    rows = await query_facts(instance_id)
    return [row for row in rows if wanted.intersection(row.get("tags") or [])]


# ── tasks ─────────────────────────────────────────────────────────────────────

async def insert_answer_cache(row: dict) -> str:
    """Insert one cached (question, answer) row. Returns its id.

    ``row`` needs ``instance_id``, ``question``, ``answer``, ``embedding`` (a list
    of floats) and optionally ``tags`` (a list), ``embedder`` (the name of the
    embedder that produced ``embedding`` — see migration 12; empty string if
    omitted, never NULL, so a lookup's embedder filter never has to special-case
    a missing stamp on a freshly-inserted row). Append-only, like ``facts``: a
    later store for the same question is a NEW row, not an overwrite — the
    lookup's cosine scan picks the best (and freshest-on-tie) match regardless.
    """
    cache_id = row.get("id") or _pkg()._new_id("ac")
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO answer_cache(
                   id, instance_id, question, answer, embedding, tags, hits,
                   creado_en, embedder)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cache_id,
                row["instance_id"],
                row["question"],
                row["answer"],
                _pkg()._dumps(list(row.get("embedding") or [])),
                _pkg()._dumps(list(row.get("tags") or [])),
                int(row.get("hits") or 0),
                row.get("creado_en") or _pkg()._now(),
                row.get("embedder") or "",
            ),
        )
        await db.commit()
    return cache_id


async def list_answer_cache(instance_id: str, limit: int = 500) -> list[dict]:
    """Return the ``limit`` most recent cached rows for ``instance_id``.

    ``embedding``/``tags`` are decoded back into lists. Scoped to ``instance_id``.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM answer_cache
               WHERE instance_id = ?
               ORDER BY creado_en DESC, rowid DESC
               LIMIT ?""",
            (instance_id, limit),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["embedding"] = _pkg()._loads(row.get("embedding"), [])
        row["tags"] = _pkg()._loads(row.get("tags"), [])
    return rows


async def bump_answer_cache_hit(instance_id: str, cache_id: str) -> None:
    """Increment the ``hits`` counter for one cached row, scoped to ``instance_id``."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            "UPDATE answer_cache SET hits = hits + 1 WHERE instance_id = ? AND id = ?",
            (instance_id, cache_id),
        )
        await db.commit()


# ── knowledge (document perception — § memory.knowledge) ────────────────────

async def insert_knowledge_chunk(row: dict) -> str:
    """Insert one indexed chunk. Returns its id.

    ``row`` needs ``instance_id``, ``source_path``, ``chunk_index``, ``text``,
    ``embedding`` (a list of floats), ``mtime`` (a float, the source file's
    mtime at ingestion time) and ``size`` (the file's byte size; together with
    ``mtime`` it's how incremental re-ingest detects a change — ``-1`` = unknown).
    """
    chunk_id = row.get("id") or _pkg()._new_id("kn")
    size = row.get("size")
    actor_id = str(row.get("actor_id") or "").strip() or Config.root_admin_id
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO knowledge(
                   id, instance_id, source_path, chunk_index, text, embedding,
                   mtime, size, ingested_at, actor_id)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk_id,
                row["instance_id"],
                row["source_path"],
                int(row.get("chunk_index") or 0),
                row["text"],
                _pkg()._dumps(list(row.get("embedding") or [])),
                float(row.get("mtime") or 0.0),
                -1 if size is None else int(size),
                row.get("ingested_at") or _pkg()._now(),
                actor_id,
            ),
        )
        await db.commit()
    return chunk_id


async def list_knowledge_files(instance_id: str) -> dict[str, tuple[float, int]]:
    """Return ``{source_path: (mtime, size)}`` for every file currently indexed.

    One row per distinct ``source_path`` (its chunks all share the same
    ``mtime``/``size``, stamped at ingestion) — used by
    :func:`lhassa.memory.knowledge.ingest` to decide, per file, whether it's
    unchanged (skip), new (index) or touched (delete old chunks, re-index).
    ``size`` is ``-1`` for rows ingested before the size column existed.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT source_path, MAX(mtime) AS mtime, MAX(size) AS size
               FROM knowledge
               WHERE instance_id = ?
               GROUP BY source_path""",
            (instance_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {
        row["source_path"]: (float(row["mtime"]), int(row["size"]))
        for row in rows
    }


async def delete_knowledge_file(instance_id: str, source_path: str) -> None:
    """Delete every chunk indexed for ``source_path`` (a re-index or a removal)."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            "DELETE FROM knowledge WHERE instance_id = ? AND source_path = ?",
            (instance_id, source_path),
        )
        await db.commit()


async def list_knowledge_chunks(instance_id: str, limit: int = 5000) -> list[dict]:
    """Return up to ``limit`` most recently ingested chunks for ``instance_id``.

    ``embedding`` is decoded back into a list of floats. Bounded so a search
    over a large index stays a cheap, in-memory cosine scan (mirrors
    ``list_answer_cache``'s bounded-recent-set pattern).
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM knowledge
               WHERE instance_id = ?
               ORDER BY ingested_at DESC, rowid DESC
               LIMIT ?""",
            (instance_id, limit),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["embedding"] = _pkg()._loads(row.get("embedding"), [])
    return rows


async def clear_knowledge(instance_id: str) -> int:
    """Wipe the whole knowledge index for ``instance_id``. Returns rows deleted."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM knowledge WHERE instance_id = ?", (instance_id,)
        )
        await db.commit()
        return cur.rowcount if cur.rowcount is not None else 0


# ── shared resources (cross-instance sharing — Stream 6 Phase A) ──────────────
# Skills/flows/templates/knowledge can be published/subscribed across instances;
# facts/episodes never appear here (see lhassa.org.sharing, the deny-by-default
# gate over these rows). Every row is scoped to instance_id like everything else.


