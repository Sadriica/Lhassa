"""
La chica — the persistent working-memory cache, DERIVED from the grande (§2.3).

The grande (``lhassa.core.db`` / brain.db) is the append-only source of truth.
The chica is a *live cache* loaded at wake: pinned facts + directivas + recent
items. It lives in its own small SQLite file (``config.chica_db_path``, default
``~/.lhassa/chica.db``) so it can be wiped independently of the grande — the
brief's "matas la chica → se regenera de la grande". It survives process restart.

Two mechanical operations (Phase 1 — NO tokens, NO LLM, NO semantics):

  - :func:`consolidate` — threshold-triggered LRU eviction. When ``size`` exceeds
    ``max_items`` we drop the least-recently-used NON-pinned items down to the
    threshold. Pinned items and directivas are NEVER evicted. ~80% mechanical.
  - :func:`regenerate` — rebuild the chica from the grande via
    ``lhassa.memory.load.load``: pinned facts + directivas + recent items.

Semantic consolidation (summarizing similar items, detecting contradictions with
a cheap LLM) is explicitly DEFERRED to Phase 2. Phase 1 is mechanical only.

Every row carries ``instance_id`` and every query filters by it (isolation from
day one). LRU order is a monotonic ``seq`` counter, so ordering is deterministic
even when many touches land in the same clock tick.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lhassa._vendor import aiosqlite

from lhassa.config import Config

# Module-level path seam — tests monkeypatch this the way they patch
# ``lhassa.core.db.DB_PATH``. Resolved at call time, so the patch takes effect.
CHICA_DB_PATH = Path(Config.chica_db_path)

# Kinds an entry can carry. ``directiva`` rows are identity and, like pinned
# rows, are never evicted by consolidate.
KIND_ITEM = "item"
KIND_FACT = "fact"
KIND_DIRECTIVA = "directiva"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _open() -> aiosqlite.Connection:
    """Open the chica DB, ensuring the directory, WAL mode, and the table exist."""
    CHICA_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(CHICA_DB_PATH)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chica (
            instance_id   TEXT NOT NULL,
            key           TEXT NOT NULL,
            content       TEXT,
            pinned        INTEGER NOT NULL DEFAULT 0,
            kind          TEXT NOT NULL DEFAULT 'item',
            seq           INTEGER NOT NULL,
            last_accessed TEXT NOT NULL,
            PRIMARY KEY (instance_id, key)
        )
        """
    )
    return conn


async def init_chica() -> None:
    """Create the chica table if missing. Idempotent."""
    conn = await _open()
    try:
        await conn.commit()
    finally:
        await conn.close()


async def _next_seq(conn: aiosqlite.Connection) -> int:
    """The next monotonic LRU sequence value (global across instances)."""
    async with conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM chica") as cur:
        row = await cur.fetchone()
    return int(row[0])


async def remember(
    instance_id: str,
    key: str,
    content: str,
    *,
    pinned: bool = False,
    kind: str = KIND_ITEM,
) -> None:
    """
    Insert an item into the chica, or touch it if it already exists (upsert).
    Either way the item becomes the most-recently-used (it gets the next ``seq``).
    """
    conn = await _open()
    try:
        seq = await _next_seq(conn)
        await conn.execute(
            """
            INSERT INTO chica(instance_id, key, content, pinned, kind, seq, last_accessed)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance_id, key) DO UPDATE SET
                content       = excluded.content,
                pinned        = excluded.pinned,
                kind          = excluded.kind,
                seq           = excluded.seq,
                last_accessed = excluded.last_accessed
            """,
            (instance_id, key, content, 1 if pinned else 0, kind, seq, _now()),
        )
        await conn.commit()
    finally:
        await conn.close()


async def touch(instance_id: str, key: str) -> bool:
    """
    Bump an item to most-recently-used (LRU). Returns True if the item existed,
    False otherwise (touching an absent key is a harmless no-op).
    """
    conn = await _open()
    try:
        seq = await _next_seq(conn)
        cur = await conn.execute(
            "UPDATE chica SET seq = ?, last_accessed = ? WHERE instance_id = ? AND key = ?",
            (seq, _now(), instance_id, key),
        )
        await conn.commit()
        return cur.rowcount > 0
    finally:
        await conn.close()


async def forget(instance_id: str, key: str) -> bool:
    """
    Drop a single item from the chica. Returns True if a row was removed.

    Used by the Phase-2 semantic consolidation pass
    (:mod:`lhassa.memory.consolidate`) to drop redundant originals once they have
    been folded into a summary. The grande is untouched, so the raw data survives.
    """
    conn = await _open()
    try:
        cur = await conn.execute(
            "DELETE FROM chica WHERE instance_id = ? AND key = ?",
            (instance_id, key),
        )
        await conn.commit()
        return cur.rowcount > 0
    finally:
        await conn.close()


async def size(instance_id: str) -> int:
    """Number of items currently cached for ``instance_id``."""
    conn = await _open()
    try:
        async with conn.execute(
            "SELECT COUNT(*) FROM chica WHERE instance_id = ?", (instance_id,)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0])
    finally:
        await conn.close()


async def items(instance_id: str) -> list[dict[str, Any]]:
    """
    Return the cached items for ``instance_id``, most-recently-used first.
    ``pinned`` is decoded to a bool.
    """
    conn = await _open()
    try:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT instance_id, key, content, pinned, kind, seq, last_accessed
               FROM chica WHERE instance_id = ?
               ORDER BY seq DESC""",
            (instance_id,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()
    for r in rows:
        r["pinned"] = bool(r["pinned"])
    return rows


async def clear(instance_id: str) -> int:
    """
    "Kill the chica" for ``instance_id`` — empty its cache. Returns how many rows
    were removed. The grande is untouched, so :func:`regenerate` rebuilds it.
    """
    conn = await _open()
    try:
        cur = await conn.execute(
            "DELETE FROM chica WHERE instance_id = ?", (instance_id,)
        )
        await conn.commit()
        return cur.rowcount
    finally:
        await conn.close()


async def consolidate(instance_id: str, *, max_items: int | None = None) -> dict:
    """
    Threshold-triggered, purely mechanical LRU consolidation ("bajar / consolidar").

    If ``size`` exceeds ``max_items``, evict the least-recently-used items until we
    are back at the threshold. NEVER evicts pinned items or directivas (identity).
    NO tokens, NO LLM, NO semantics — the ~80% mechanical path. Semantic
    consolidation (summarizing similar items, contradiction detection) is DEFERRED
    to Phase 2.

    Returns ``{"kept": int, "evicted": int}``. Note: if the protected (pinned +
    directiva) rows alone exceed ``max_items``, ``kept`` stays above the threshold
    — protected rows are never dropped to honor it.
    """
    if max_items is None:
        max_items = Config.chica_max_items

    conn = await _open()
    try:
        async with conn.execute(
            "SELECT COUNT(*) FROM chica WHERE instance_id = ?", (instance_id,)
        ) as cur:
            total = int((await cur.fetchone())[0])

        if total <= max_items:
            return {"kept": total, "evicted": 0}

        # Evictable = NON-pinned, NON-directiva. Least-recently-used first.
        async with conn.execute(
            """SELECT key FROM chica
               WHERE instance_id = ? AND pinned = 0 AND kind != ?
               ORDER BY seq ASC""",
            (instance_id, KIND_DIRECTIVA),
        ) as cur:
            evictable = [r[0] for r in await cur.fetchall()]

        want_to_evict = total - max_items
        to_evict = evictable[:want_to_evict]

        if to_evict:
            await conn.executemany(
                "DELETE FROM chica WHERE instance_id = ? AND key = ?",
                [(instance_id, k) for k in to_evict],
            )
            await conn.commit()

        evicted = len(to_evict)
        return {"kept": total - evicted, "evicted": evicted}
    finally:
        await conn.close()


async def regenerate(instance_id: str, *, token_budget: int, active_tags=()) -> int:
    """
    Rebuild the chica from the grande ("matas la chica → se regenera de la grande").

    Wipes the instance's cache, then re-populates it from
    :func:`lhassa.memory.load.load`: directivas (identity) + pinned facts + active
    tag facts + recent episodes. Returns the number of entries loaded.

    This is the regeneration source of truth: the grande survives even when the
    chica is destroyed, so the live cache can always be reconstituted.
    """
    # Imported lazily to avoid a circular import (load → facts → db; chica is a
    # peer that calls into load only at regeneration time).
    from lhassa.memory.load import load

    ctx = await load(instance_id, token_budget=token_budget, active_tags=active_tags)

    await clear(instance_id)

    loaded = 0
    # Directivas — identity; pinned, never evicted.
    for i, directive in enumerate(ctx.directives):
        await remember(
            instance_id, f"directiva:{i}", directive,
            pinned=True, kind=KIND_DIRECTIVA,
        )
        loaded += 1
    # Pinned facts — identity datos; pinned, never evicted.
    for fact in ctx.pinned_facts:
        await remember(
            instance_id, f"fact:{fact.id}", fact.contenido,
            pinned=True, kind=KIND_FACT,
        )
        loaded += 1
    # Active-context facts (by tag) — evictable.
    for fact in ctx.tag_facts:
        await remember(
            instance_id, f"fact:{fact.id}", fact.contenido,
            pinned=False, kind=KIND_FACT,
        )
        loaded += 1
    # Recent episodes — evictable, recency layer.
    for ep in ctx.episodes:
        ep_id = ep.get("id") or ep.get("creado_en") or str(loaded)
        content = ep.get("resultado") or ep.get("despertar_por") or ""
        await remember(
            instance_id, f"episode:{ep_id}", content,
            pinned=False, kind=KIND_ITEM,
        )
        loaded += 1

    return loaded
