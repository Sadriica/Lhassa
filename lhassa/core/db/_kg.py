# SPDX-License-Identifier: AGPL-3.0-or-later
"""The persisted knowledge graph (entities + edges).

Split out of the former monolithic ``lhassa/core/db.py``; part of the
``lhassa.core.db`` facade package (see ``lhassa/core/db/__init__.py``).
"""

from __future__ import annotations

from lhassa._vendor import aiosqlite

from ._conn import _pkg

async def upsert_kg_entity(row: dict) -> str:
    """Insert-or-touch one entity by (instance_id, name). Returns its id."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id FROM kg_entities WHERE instance_id = ? AND name = ?",
            (row["instance_id"], row["name"]),
        ) as cur:
            existing = await cur.fetchone()
        if existing is not None:
            await db.execute(
                "UPDATE kg_entities SET last_seen = ?, kind = ? WHERE id = ?",
                (row["last_seen"], row.get("kind") or "term", existing["id"]),
            )
            await db.commit()
            return existing["id"]
        await db.execute(
            """INSERT INTO kg_entities(id, instance_id, name, kind, first_seen, last_seen)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["instance_id"],
                row["name"],
                row.get("kind") or "term",
                row["first_seen"],
                row["last_seen"],
            ),
        )
        await db.commit()
        return row["id"]


async def upsert_kg_edge(row: dict) -> None:
    """Insert one edge or accumulate its weight (co-occurrence counting)."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO kg_edges(
                   instance_id, from_id, to_id, kind, weight, source, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(instance_id, from_id, to_id, kind)
               DO UPDATE SET weight = weight + excluded.weight,
                             updated_at = excluded.updated_at""",
            (
                row["instance_id"],
                row["from_id"],
                row["to_id"],
                row["kind"],
                float(row.get("weight") or 1.0),
                row.get("source"),
                row["created_at"],
                row["updated_at"],
            ),
        )
        await db.commit()


async def kg_neighbors(instance_id: str, entity_ids: "list[str]", limit: int = 50) -> "list[dict]":
    """1-hop edges touching any of ``entity_ids`` (either direction), heaviest first."""
    if not entity_ids:
        return []
    marks = ",".join("?" for _ in entity_ids)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""SELECT * FROM kg_edges
                WHERE instance_id = ? AND (from_id IN ({marks}) OR to_id IN ({marks}))
                ORDER BY weight DESC LIMIT ?""",
            [instance_id, *entity_ids, *entity_ids, limit],
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def list_kg_entities(instance_id: str, limit: int = 200) -> "list[dict]":
    """Entities for this instance, most-recently-seen first."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM kg_entities WHERE instance_id = ?
               ORDER BY last_seen DESC LIMIT ?""",
            (instance_id, limit),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


# ── loops (bounded verifier-terminated iteration — loop engineering) ─────────


async def merge_kg_entities(instance_id: str, keep_id: str, drop_id: str) -> int:
    """Merge entity ``drop_id`` into ``keep_id``: repoint every edge, then
    remove the dropped entity. Edges that would collide accumulate weight into
    the surviving edge (same ON CONFLICT rule as upsert). Returns edges moved.
    """
    if keep_id == drop_id:
        return 0
    moved = 0
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM kg_edges WHERE instance_id = ? AND (from_id = ? OR to_id = ?)",
            (instance_id, drop_id, drop_id),
        ) as cur:
            edges = [dict(r) for r in await cur.fetchall()]
        for edge in edges:
            new_from = keep_id if edge["from_id"] == drop_id else edge["from_id"]
            new_to = keep_id if edge["to_id"] == drop_id else edge["to_id"]
            if new_from == new_to:
                # A self-edge born from the merge is meaningless — drop it.
                await db.execute(
                    "DELETE FROM kg_edges WHERE instance_id = ? AND from_id = ? AND to_id = ? AND kind = ?",
                    (instance_id, edge["from_id"], edge["to_id"], edge["kind"]),
                )
                continue
            await db.execute(
                "DELETE FROM kg_edges WHERE instance_id = ? AND from_id = ? AND to_id = ? AND kind = ?",
                (instance_id, edge["from_id"], edge["to_id"], edge["kind"]),
            )
            await db.execute(
                """INSERT INTO kg_edges(
                       instance_id, from_id, to_id, kind, weight, source,
                       created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(instance_id, from_id, to_id, kind)
                   DO UPDATE SET weight = weight + excluded.weight,
                                 updated_at = excluded.updated_at""",
                (
                    instance_id, new_from, new_to, edge["kind"], edge["weight"],
                    edge.get("source"), edge["created_at"], edge["updated_at"],
                ),
            )
            moved += 1
        await db.execute(
            "DELETE FROM kg_entities WHERE instance_id = ? AND id = ?",
            (instance_id, drop_id),
        )
        await db.commit()
    return moved

