# SPDX-License-Identifier: AGPL-3.0-or-later
"""Org messages (inbox), commons, and cross-instance sharing.

Split out of the former monolithic ``lhassa/core/db.py``; part of the
``lhassa.core.db`` facade package (see ``lhassa/core/db/__init__.py``).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from lhassa._vendor import aiosqlite

from ._conn import _pkg

async def enqueue_org_message(
    instance_id: str,
    *,
    kind: str,
    from_instance: str,
    to_instance: str,
    payload: Optional[dict] = None,
    corr_id: str = "",
    status: str = "pending",
    msg_id: Optional[str] = None,
    creado_en: Optional[str] = None,
) -> str:
    """Persist one typed inter-brain message. Returns its id (generated if absent).

    ``payload`` is JSON-encoded. Scoped to ``instance_id`` (the org/tenant).
    """
    msg_id = msg_id or _pkg()._new_id("msg")
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO org_messages(
                   id, instance_id, kind, from_instance, to_instance,
                   payload, status, corr_id, creado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id, instance_id, kind, from_instance, to_instance,
                _pkg()._dumps(payload or {}), status, corr_id, creado_en or _pkg()._now(),
            ),
        )
        await db.commit()
    return msg_id


def _decode_org_message(row: dict) -> dict:
    """Decode the JSON ``payload`` column of an org_messages row in place."""
    row["payload"] = _pkg()._loads(row.get("payload"), {})
    return row


async def get_org_inbox(
    instance_id: str,
    to_instance: str,
    *,
    status: Optional[str] = None,
) -> list[dict]:
    """Return messages addressed to ``to_instance`` (oldest first), payload decoded.

    Optionally filtered by ``status``. Always scoped to ``instance_id``.
    """
    sql = "SELECT * FROM org_messages WHERE instance_id = ? AND to_instance = ?"
    args: list[Any] = [instance_id, to_instance]
    if status is not None:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY creado_en, rowid"
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    return [_decode_org_message(r) for r in rows]


async def get_org_messages_by_corr(
    instance_id: str,
    corr_id: str,
    *,
    kind: Optional[str] = None,
) -> list[dict]:
    """Return messages sharing a ``corr_id`` (oldest first), payload decoded.

    Optionally filtered by ``kind`` (e.g. only TASK_RESULT). Scoped to instance.
    """
    sql = "SELECT * FROM org_messages WHERE instance_id = ? AND corr_id = ?"
    args: list[Any] = [instance_id, corr_id]
    if kind is not None:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY creado_en, rowid"
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    return [_decode_org_message(r) for r in rows]


async def get_org_message(instance_id: str, msg_id: str) -> Optional[dict]:
    """Return a single message row (payload decoded), or ``None`` if absent."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM org_messages WHERE instance_id = ? AND id = ?",
            (instance_id, msg_id),
        ) as cur:
            row = await cur.fetchone()
    return _decode_org_message(dict(row)) if row is not None else None


async def set_org_message_status(instance_id: str, msg_id: str, status: str) -> None:
    """Advance a message's ``status`` (pending → delivered → done). Instance-scoped."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            "UPDATE org_messages SET status = ? WHERE instance_id = ? AND id = ?",
            (status, instance_id, msg_id),
        )
        await db.commit()


# ── org_commons (the read-only cross-cutting source of truth. Fase 7) ───────────
#
# ONE shared row per (instance_id, clave). Reads are open to all brains; the WRITE
# gate (curator-only) lives in lhassa.org.commons + lhassa.org.rbac, not here; db
# stays a dumb store. Values are JSON-encoded to preserve dict/list/str/number.


async def set_commons(
    instance_id: str,
    clave: str,
    valor: Any,
    *,
    curator: str,
) -> None:
    """Upsert one commons entry (single shared row per key). Records ``curator``.

    JSON-encodes ``valor`` to preserve its type. Scoped to ``instance_id``.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO org_commons(instance_id, clave, valor, curator, actualizado_en)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(instance_id, clave)
               DO UPDATE SET valor = excluded.valor,
                             curator = excluded.curator,
                             actualizado_en = excluded.actualizado_en""",
            (instance_id, clave, json.dumps(valor, ensure_ascii=False), curator, _pkg()._now()),
        )
        await db.commit()


async def get_commons(instance_id: str, clave: str) -> Optional[dict]:
    """Return one commons row (value JSON-decoded) or ``None`` if absent.

    The returned dict carries ``clave``, ``valor`` (decoded), ``curator`` and
    ``actualizado_en``. Scoped to ``instance_id``.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM org_commons WHERE instance_id = ? AND clave = ?",
            (instance_id, clave),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["valor"] = _pkg()._loads(out.get("valor"), None)
    return out


async def get_all_commons(instance_id: str) -> dict[str, Any]:
    """Return the whole commons as ``{clave: valor}`` (values decoded), instance-scoped."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT clave, valor FROM org_commons WHERE instance_id = ? ORDER BY clave",
            (instance_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {row["clave"]: _pkg()._loads(row["valor"], None) for row in rows}


# ── watch_state (watchlist dedup key/value store. Phase 4) ──────────────────────
#
# A generic, lightweight key/value store for watchlist transition dedup. Each
# sense writes its last-seen state here so a persistent condition fires ONCE per
# transition rather than every 2-minute heartbeat beat. Keys are per-sense
# identifiers (e.g. ``repo:<path>``, ``health:level``); values are opaque strings.


async def publish_resource(
    instance_id: str,
    resource_type: str,
    resource_name: str,
    *,
    actor_id: str,
) -> str:
    """Publish (or re-publish, clearing any prior revocation) one shared resource.

    Idempotent on ``(instance_id, resource_type, resource_name)``: republishing
    an existing offer updates its ``actor_id``/``published_at`` and clears
    ``revoked_at`` rather than duplicating a row.
    """
    row_id = _pkg()._new_id("share")
    now = _pkg()._now()
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO shared_resources(
                   id, instance_id, resource_type, resource_name, actor_id,
                   published_at, revoked_at)
               VALUES(?, ?, ?, ?, ?, ?, NULL)
               ON CONFLICT(instance_id, resource_type, resource_name)
               DO UPDATE SET actor_id = excluded.actor_id,
                             published_at = excluded.published_at,
                             revoked_at = NULL""",
            (row_id, instance_id, resource_type, resource_name, actor_id, now),
        )
        await db.commit()
    return row_id


async def unpublish_resource(
    instance_id: str, resource_type: str, resource_name: str
) -> bool:
    """Mark a published resource revoked (soft-delete). Returns whether a row changed."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        cur = await db.execute(
            """UPDATE shared_resources SET revoked_at = ?
               WHERE instance_id = ? AND resource_type = ? AND resource_name = ?
                 AND revoked_at IS NULL""",
            (_pkg()._now(), instance_id, resource_type, resource_name),
        )
        await db.commit()
        return bool(cur.rowcount)


async def list_shared_resources(
    instance_id: str, *, resource_type: "Optional[str]" = None
) -> list[dict]:
    """Live (non-revoked) offerings published by ``instance_id``."""
    sql = "SELECT * FROM shared_resources WHERE instance_id = ? AND revoked_at IS NULL"
    args: list[Any] = [instance_id]
    if resource_type:
        sql += " AND resource_type = ?"
        args.append(resource_type)
    sql += " ORDER BY published_at"
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_shared_resource(
    instance_id: str, resource_type: str, resource_name: str
) -> "Optional[dict]":
    """One live (non-revoked) offering, or ``None``."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM shared_resources
               WHERE instance_id = ? AND resource_type = ? AND resource_name = ?
                 AND revoked_at IS NULL""",
            (instance_id, resource_type, resource_name),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def add_resource_subscription(
    instance_id: str,
    source_instance_id: str,
    resource_type: str,
    resource_name: str,
    *,
    actor_id: str,
) -> str:
    """Record a (permitted) subscription. Idempotent; re-subscribing clears any
    prior ``unsubscribed_at`` instead of duplicating a row."""
    row_id = _pkg()._new_id("sub")
    now = _pkg()._now()
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO resource_subscriptions(
                   id, instance_id, source_instance_id, resource_type,
                   resource_name, actor_id, subscribed_at, unsubscribed_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, NULL)
               ON CONFLICT(instance_id, source_instance_id, resource_type, resource_name)
               DO UPDATE SET actor_id = excluded.actor_id,
                             subscribed_at = excluded.subscribed_at,
                             unsubscribed_at = NULL""",
            (row_id, instance_id, source_instance_id, resource_type, resource_name, actor_id, now),
        )
        await db.commit()
    return row_id


async def remove_resource_subscription(
    instance_id: str, source_instance_id: str, resource_type: str, resource_name: str
) -> bool:
    """Mark a subscription unsubscribed. Returns whether a row changed."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        cur = await db.execute(
            """UPDATE resource_subscriptions SET unsubscribed_at = ?
               WHERE instance_id = ? AND source_instance_id = ? AND resource_type = ?
                 AND resource_name = ? AND unsubscribed_at IS NULL""",
            (_pkg()._now(), instance_id, source_instance_id, resource_type, resource_name),
        )
        await db.commit()
        return bool(cur.rowcount)


async def list_resource_subscriptions(instance_id: str) -> list[dict]:
    """Live (active) subscriptions held by ``instance_id``."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM resource_subscriptions
               WHERE instance_id = ? AND unsubscribed_at IS NULL
               ORDER BY subscribed_at""",
            (instance_id,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


# ── projects (the first-class workspace entity; § core.projects) ─────────────


