# SPDX-License-Identifier: AGPL-3.0-or-later
"""Approvals, owner prompts, autonomy streaks, postulaciones, watch_state.

Split out of the former monolithic ``lhassa/core/db.py``; part of the
``lhassa.core.db`` facade package (see ``lhassa/core/db/__init__.py``).
"""

from __future__ import annotations

from typing import Any, Optional

from lhassa._vendor import aiosqlite
from lhassa.config import Config

from ._conn import _pkg

async def add_approval(
    instance_id: str,
    tipo: str,
    payload: Optional[dict] = None,
    *,
    estado: str = "pendiente",
    approval_id: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> str:
    """Enqueue a human-in-the-loop approval. Returns its id.

    ``actor_id`` (WHO proposed it) is promoted to a real column (Stream 6 Phase
    A): given explicitly, else recovered from ``payload["actor_id"]``
    (``Origin.actor_id`` already threads it there), else the owner.
    """
    approval_id = approval_id or _pkg()._new_id("ap")
    actor_id = (
        str(actor_id or "").strip()
        or str((payload or {}).get("actor_id") or "").strip()
        or Config.root_admin_id
    )
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO approvals(
                   id, instance_id, tipo, payload, estado, creado_en, actor_id)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, instance_id, tipo, _pkg()._dumps(payload or {}), estado, _pkg()._now(), actor_id),
        )
        await db.commit()
    return approval_id


async def load_pending_approvals(instance_id: str) -> list[dict]:
    """Return pending approvals for ``instance_id`` (oldest first), payload decoded."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM approvals
               WHERE instance_id = ? AND estado = 'pendiente'
               ORDER BY creado_en""",
            (instance_id,),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["payload"] = _pkg()._loads(row.get("payload"), {})
    return rows


async def add_owner_prompt(
    instance_id: str,
    *,
    kind: str,
    slot: str = "",
    prompt: str = "",
    expires_at: str,
) -> str:
    """Add one pending owner-facing prompt, replacing older pending prompts of same kind."""
    now = _pkg()._now()
    prompt_id = _pkg()._new_id("op")
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """UPDATE owner_prompts
                  SET status = 'superseded'
                WHERE instance_id = ? AND kind = ? AND status = 'pending'""",
            (instance_id, kind),
        )
        await db.execute(
            """INSERT INTO owner_prompts(
                   id, instance_id, kind, slot, prompt, status,
                   created_at, expires_at)
               VALUES(?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (prompt_id, instance_id, kind, slot, prompt, now, expires_at),
        )
        await db.commit()
    return prompt_id


async def pending_owner_prompt(
    instance_id: str,
    *,
    kind: str,
    now: Optional[str] = None,
) -> Optional[dict]:
    """Return the newest unexpired pending owner prompt for ``kind``."""
    now = now or _pkg()._now()
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM owner_prompts
               WHERE instance_id = ?
                 AND kind = ?
                 AND status = 'pending'
                 AND expires_at >= ?
               ORDER BY created_at DESC, rowid DESC
               LIMIT 1""",
            (instance_id, kind, now),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def consume_owner_prompt(
    instance_id: str,
    prompt_id: str,
    *,
    answer: str,
) -> None:
    """Mark a pending owner prompt as answered."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """UPDATE owner_prompts
                  SET status = 'answered', answered_at = ?, answer = ?
                WHERE instance_id = ? AND id = ? AND status = 'pending'""",
            (_pkg()._now(), answer, instance_id, prompt_id),
        )
        await db.commit()


async def get_approval(instance_id: str, approval_id: str) -> Optional[dict]:
    """Return a single approval row (payload decoded), or ``None`` if absent.

    Scoped to ``instance_id`` (isolation from day one). Used when resolving an
    approval so the autonomy knob can be fed the action/actor it was parked for.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM approvals WHERE instance_id = ? AND id = ?",
            (instance_id, approval_id),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["payload"] = _pkg()._loads(out.get("payload"), {})
    return out


async def has_approved_task_approval(instance_id: str, task_id: str) -> bool:
    """Whether a resolved mint approval authorizes this exact task id."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT payload FROM approvals
               WHERE instance_id = ? AND estado = 'aprobada'""",
            (instance_id,),
        ) as cur:
            rows = await cur.fetchall()
    for row in rows:
        payload = _pkg()._loads(row["payload"], {})
        if (
            isinstance(payload, dict)
            and payload.get("source") == "mint_task"
            and payload.get("task_id") == task_id
        ):
            return True
    return False


async def resolve_approval(instance_id: str, approval_id: str, estado: str) -> bool:
    """CAS a pending approval and return whether this caller resolved it."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        cur = await db.execute(
            """UPDATE approvals
               SET estado = ?, resuelto_en = ?
               WHERE instance_id = ? AND id = ? AND estado = 'pendiente'""",
            (estado, _pkg()._now(), instance_id, approval_id),
        )
        await db.commit()
    return cur.rowcount == 1


# ── autonomy (the friction knob — consecutive human approvals) ──────────────────

async def get_autonomy_streak(instance_id: str, accion: str, actor_id: str) -> int:
    """Return the consecutive-human-approval streak for one (accion, actor_id).

    Scoped to ``instance_id``. Missing row → ``0`` (no autonomy earned yet).
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        async with db.execute(
            """SELECT streak FROM autonomy
               WHERE instance_id = ? AND accion = ? AND actor_id = ?""",
            (instance_id, accion, actor_id),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def bump_autonomy_streak(instance_id: str, accion: str, actor_id: str) -> int:
    """Increment (or create) the streak for one (accion, actor_id). Returns the new value.

    Upsert: a HUMANO-origin approval advances the count by one. This only ever
    *records* how often a human said yes; it never touches trust levels.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO autonomy(instance_id, accion, actor_id, streak, actualizado_en)
               VALUES(?, ?, ?, 1, ?)
               ON CONFLICT(instance_id, accion, actor_id)
               DO UPDATE SET streak = streak + 1, actualizado_en = excluded.actualizado_en""",
            (instance_id, accion, actor_id, _pkg()._now()),
        )
        await db.commit()
    return await get_autonomy_streak(instance_id, accion, actor_id)


async def reset_autonomy_streak(instance_id: str, accion: str, actor_id: str) -> None:
    """Reset the streak for one (accion, actor_id) to zero (a denial resets it)."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO autonomy(instance_id, accion, actor_id, streak, actualizado_en)
               VALUES(?, ?, ?, 0, ?)
               ON CONFLICT(instance_id, accion, actor_id)
               DO UPDATE SET streak = 0, actualizado_en = excluded.actualizado_en""",
            (instance_id, accion, actor_id, _pkg()._now()),
        )
        await db.commit()


# ── postulaciones (tracked applications — tracking_postulaciones task) ───────────

async def add_postulacion(
    instance_id: str,
    empresa: str,
    rol: str = "",
    *,
    estado: str = "pendiente",
    fuente: str = "",
    link: str = "",
    notas: str = "",
    postulacion_id: Optional[str] = None,
) -> str:
    """Insert one tracked application. Returns its id (generated if not supplied).

    Scoped to ``instance_id`` (isolation from day one).
    """
    postulacion_id = postulacion_id or _pkg()._new_id("post")
    now = _pkg()._now()
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO postulaciones(
                   id, instance_id, empresa, rol, estado, fuente, link, notas,
                   creado_en, actualizado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                postulacion_id, instance_id, empresa, rol, estado, fuente, link,
                notas, now, now,
            ),
        )
        await db.commit()
    return postulacion_id


async def update_postulacion_estado(
    instance_id: str,
    postulacion_id: str,
    estado: str,
    *,
    notas: Optional[str] = None,
) -> bool:
    """Advance one application's ``estado`` (and optionally append/replace notas).

    Returns True if a row was updated, False if no such id under ``instance_id``.
    Scoped to ``instance_id``.
    """
    sets = ["estado = ?", "actualizado_en = ?"]
    args: list[Any] = [estado, _pkg()._now()]
    if notas is not None:
        sets.append("notas = ?")
        args.append(notas)
    args.extend([instance_id, postulacion_id])
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE postulaciones SET {', '.join(sets)} "
            "WHERE instance_id = ? AND id = ?",
            args,
        )
        await db.commit()
        return cur.rowcount > 0


async def list_postulaciones(
    instance_id: str,
    *,
    estado: Optional[str] = None,
) -> list[dict]:
    """Return tracked applications for ``instance_id`` (newest first).

    Optionally filtered by ``estado``. Always scoped to ``instance_id``.
    """
    sql = "SELECT * FROM postulaciones WHERE instance_id = ?"
    args: list[Any] = [instance_id]
    if estado is not None:
        sql += " AND estado = ?"
        args.append(estado)
    sql += " ORDER BY creado_en DESC, rowid DESC"
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            return [dict(row) for row in await cur.fetchall()]


# ── artifacts (append-only work products — "Lhassa is the sender of record") ────

async def get_watch_state(instance_id: str, key: str) -> Optional[str]:
    """Return the stored value for ``key``, or ``None`` if not yet set.

    Scoped to ``instance_id`` so different brain instances don't cross-talk.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        async with conn.execute(
            "SELECT value FROM watch_state WHERE instance_id = ? AND key = ?",
            (instance_id, key),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row is not None else None


async def set_watch_state(instance_id: str, key: str, value: str) -> None:
    """Upsert the state for ``key`` to ``value``, scoped to ``instance_id``."""
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO watch_state(instance_id, key, value, updated_en)
               VALUES(?, ?, ?, ?)
               ON CONFLICT(instance_id, key)
               DO UPDATE SET value = excluded.value,
                             updated_en = excluded.updated_en""",
            (instance_id, key, value, _pkg()._now()),
        )
        await conn.commit()


# ── jobs: ack surface (Phase 4 watchlist job_review sense) ───────────────────────
#
# ``acked`` prevents the ``_from_finished_jobs`` watchlist sense from re-firing for
# the same finished job on every subsequent tick. The sense marks ``acked=1`` as
# it fires, so each job_review Trigger is emitted exactly once.


