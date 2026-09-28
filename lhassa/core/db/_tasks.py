# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tasks, jobs, desired state, deferred-task retry sweep, and loops.

Split out of the former monolithic ``lhassa/core/db.py``; part of the
``lhassa.core.db`` facade package (see ``lhassa/core/db/__init__.py``).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from lhassa._vendor import aiosqlite

from ._conn import _pkg

async def add_task(
    instance_id: str,
    accion: str,
    objetivo: str = "",
    params: Optional[dict] = None,
    *,
    origen_trust: int = 1,
    origen_actor: Optional[str] = None,
    estado: str = "pendiente",
    firma: str = "",
    task_id: Optional[str] = None,
    due_at: Optional[str] = None,
    recurrence: Optional[str] = None,
    creado_en: Optional[str] = None,
) -> str:
    """Insert a task. Returns its id (generated if not supplied).

    ``due_at`` is an ISO 8601 UTC timestamp; a task with a future ``due_at``
    is NOT returned by :func:`load_pending_tasks` until its time arrives.
    ``recurrence`` is an interval in SECONDS (stored as a string); the brain
    mints the next occurrence when it marks this task "hecha".
    """
    task_id = task_id or _pkg()._new_id("t")
    now = _pkg()._now()
    created = creado_en or now
    if not firma:
        # Secure-by-default local persistence: every task entering through the
        # brain's write seam gets a signed execution envelope. Direct SQL remains
        # untrusted and is rejected by the heartbeat gate.
        from lhassa.core.task import Task
        from lhassa.security import signing
        from lhassa.security.trust import Origin, TrustLevel

        task = Task(
            id=task_id,
            instance_id=instance_id,
            accion=accion,
            objetivo=objetivo,
            params=dict(params or {}),
            origen=Origin(trust=TrustLevel(origen_trust), actor_id=origen_actor),
            estado=estado,
            creado_en=created,
        )
        firma = signing.sign_task(task)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO tasks(
                   id, instance_id, accion, objetivo, params,
                   origen_trust, origen_actor, estado, firma, creado_en,
                   due_at, recurrence, updated_en)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, instance_id, accion, objetivo, _pkg()._dumps(params or {}),
                origen_trust, origen_actor, estado, firma, created,
                due_at, recurrence, now,
            ),
        )
        await db.commit()
    return task_id


async def load_pending_tasks(instance_id: str) -> list[dict]:
    """Return pending tasks for ``instance_id`` (oldest first), params decoded.

    Due-aware: a task with a ``due_at`` timestamp in the future is NOT returned
    until its scheduled time arrives. One-shot tasks (``due_at IS NULL``) are
    always returned when pendiente.
    """
    now = _pkg()._now()
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM tasks
               WHERE instance_id = ? AND estado = 'pendiente'
                 AND (due_at IS NULL OR due_at <= ?)
               ORDER BY creado_en""",
            (instance_id, now),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["params"] = _pkg()._loads(row.get("params"), {})
    return rows


async def get_task(instance_id: str, task_id: str) -> Optional[dict]:
    """Return one task by id, scoped to ``instance_id``, with decoded params."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tasks WHERE instance_id = ? AND id = ?",
            (instance_id, task_id),
        ) as cur:
            raw = await cur.fetchone()
    if raw is None:
        return None
    row = dict(raw)
    row["params"] = _pkg()._loads(row.get("params"), {})
    return row


async def list_tasks(
    instance_id: str,
    *,
    estado: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """Return tasks for ``instance_id`` (newest first, cap ``limit``).

    Optionally filtered by ``estado``. Params decoded. Always scoped to
    ``instance_id``.
    """
    sql = "SELECT * FROM tasks WHERE instance_id = ?"
    args: list[Any] = [instance_id]
    if estado is not None:
        sql += " AND estado = ?"
        args.append(estado)
    sql += " ORDER BY creado_en DESC, rowid DESC LIMIT ?"
    args.append(limit)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["params"] = _pkg()._loads(row.get("params"), {})
    return rows


async def set_task_state(instance_id: str, task_id: str, estado: str) -> None:
    """Update a task's state, scoped to ``instance_id``.

    Also stamps ``updated_en``: the deferred-retry sweep uses it to know how
    long a task has sat "diferida" before it is due for a retry.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET estado = ?, updated_en = ? WHERE instance_id = ? AND id = ?",
            (estado, _pkg()._now(), instance_id, task_id),
        )
        await db.commit()


async def set_task_recurrence(
    task_id: str, recurrence: str, *, due_at: Optional[str] = None
) -> None:
    """Retune a recurring task's interval (SECONDS, stored as a string).

    The cadence of a standing task is a DECLARATION (see
    ``lhassa.seeds.maintenance._RECURRING_ACTIONS``), and until this existed a
    seed could only mint or skip: editing the declared interval changed nothing
    for a brain that was already seeded, so the edit looked applied and was
    not.

    ``due_at`` moves the NEXT occurrence too, and a retune that shortens the
    interval must pass it: the next run was scheduled under the OLD cadence, so
    a weekly task retuned to daily still sleeps until its original weekly slot
    and the change appears to have done nothing for six more days. Half an
    applied fix is the failure mode this whole function exists to close.

    Deliberately not scoped by instance: the caller reads the row it is
    retuning from the instance-scoped listing first.
    """
    sets = "recurrence = ?, updated_en = ?"
    args: list = [str(recurrence), _pkg()._now()]
    if due_at is not None:
        sets += ", due_at = ?"
        args.append(due_at)
    args.append(task_id)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(f"UPDATE tasks SET {sets} WHERE id = ?", tuple(args))
        await db.commit()


# ── deferred-task retry sweep (Phase-Resilience) ──────────────────────────────
#
# A task the judgment path could not run (no usable provider, or the daily
# budget spent) is marked "diferida" and, today, never revisited; the provider
# outage/budget window can pass and the task simply rots. This sweep re-queues
# a "diferida" task back to "pendiente" once it has sat for at least
# ``backoff_seconds``, tracking a retry counter in its params; once that counter
# reaches ``max_retries`` the task is marked "rechazada" (terminal; never
# retried again) instead of being requeued forever.


async def sweep_deferred_tasks(
    instance_id: str, *, backoff_seconds: float, max_retries: int
) -> dict:
    """Re-queue stale "diferida" tasks; reject the ones that exhausted retries.

    A "diferida" task whose ``updated_en`` is older than ``backoff_seconds`` is
    either requeued to "pendiente" (with ``params["_retries"]`` incremented) or,
    if it has already reached ``max_retries``, marked "rechazada" (terminal).
    Returns ``{"requeued": int, "rejected_ids": [task_id, ...]}``. Scoped to
    ``instance_id``. This function itself does not raise on a normal DB error
    path (the caller (``step()``) additionally wraps the call in a
    try/except so a sweep failure can never crash a heartbeat tick).
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=max(0.0, backoff_seconds))
    ).isoformat()

    requeued = 0
    rejected_ids: list[str] = []
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM tasks
               WHERE instance_id = ? AND estado = 'diferida'
                 AND (updated_en IS NULL OR updated_en <= ?)""",
            (instance_id, cutoff),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]

        now = _pkg()._now()
        for row in rows:
            params = _pkg()._loads(row.get("params"), {})
            if not isinstance(params, dict):
                params = {}
            retries = int(params.get("_retries") or 0)
            if retries >= max(0, max_retries):
                await db.execute(
                    "UPDATE tasks SET estado = 'rechazada', updated_en = ? "
                    "WHERE instance_id = ? AND id = ?",
                    (now, instance_id, row["id"]),
                )
                rejected_ids.append(row["id"])
            else:
                params["_retries"] = retries + 1
                await db.execute(
                    "UPDATE tasks SET estado = 'pendiente', params = ?, updated_en = ? "
                    "WHERE instance_id = ? AND id = ?",
                    (_pkg()._dumps(params), now, instance_id, row["id"]),
                )
                requeued += 1
        await db.commit()

    return {"requeued": requeued, "rejected_ids": rejected_ids}


# ── jobs ──────────────────────────────────────────────────────────────────────

async def record_job(
    instance_id: str,
    *,
    pid: Optional[int] = None,
    harness: str = "",
    estado: str = "running",
    costo_usd: float = 0.0,
    job_id: Optional[str] = None,
) -> str:
    """Record a launched job (long-running harness process). Returns its id."""
    job_id = job_id or _pkg()._new_id("j")
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO jobs(
                   id, instance_id, pid, harness, estado, costo_usd, creado_en)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (job_id, instance_id, pid, harness, estado, costo_usd, _pkg()._now()),
        )
        await db.commit()
    return job_id


async def update_job(
    instance_id: str,
    job_id: str,
    *,
    estado: Optional[str] = None,
    costo_usd: Optional[float] = None,
    terminado_en: Optional[str] = None,
) -> None:
    """Update a job's mutable bookkeeping fields, scoped to ``instance_id``."""
    sets: list[str] = []
    args: list[Any] = []
    if estado is not None:
        sets.append("estado = ?")
        args.append(estado)
    if costo_usd is not None:
        sets.append("costo_usd = ?")
        args.append(costo_usd)
    if terminado_en is not None:
        sets.append("terminado_en = ?")
        args.append(terminado_en)
    if not sets:
        return
    args.extend([instance_id, job_id])
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE instance_id = ? AND id = ?",
            args,
        )
        await db.commit()


# ── desired_state ─────────────────────────────────────────────────────────────

async def upsert_desired_state(
    instance_id: str, machine_id: str, clave: str, valor: str
) -> None:
    """Set the reconciliation target for one (machine, key), scoped to instance."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO desired_state(
                   instance_id, machine_id, clave, valor, actualizado_en)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(instance_id, machine_id, clave)
               DO UPDATE SET valor = excluded.valor,
                             actualizado_en = excluded.actualizado_en""",
            (instance_id, machine_id, clave, valor, _pkg()._now()),
        )
        await db.commit()


async def get_desired_state(instance_id: str, machine_id: str) -> dict[str, str]:
    """Return the desired ``{clave: valor}`` map for one machine in one instance."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT clave, valor FROM desired_state
               WHERE instance_id = ? AND machine_id = ?
               ORDER BY clave""",
            (instance_id, machine_id),
        ) as cur:
            return {row["clave"]: row["valor"] for row in await cur.fetchall()}


# ── approvals ─────────────────────────────────────────────────────────────────

async def load_unacked_finished_jobs(instance_id: str) -> list[dict]:
    """Return finished (done/error) jobs that have not yet been acked.

    A job is "finished" when its ``estado`` is either ``done`` or starts with
    ``error`` (runner.py uses ``"error: harness unavailable"`` and plain
    ``"error"``). Sorted oldest-first. Scoped to ``instance_id``.
    """
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM jobs
               WHERE instance_id = ?
                 AND acked = 0
                 AND (estado = 'done' OR estado LIKE 'error%')
               ORDER BY creado_en""",
            (instance_id,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def ack_job(instance_id: str, job_id: str) -> None:
    """Mark a job as acked (job_review Trigger already fired). Scoped to instance."""
    async with aiosqlite.connect(_pkg().DB_PATH) as conn:
        await conn.execute(
            "UPDATE jobs SET acked = 1 WHERE instance_id = ? AND id = ?",
            (instance_id, job_id),
        )
        await conn.commit()


# ── answer_cache (the "$0 intelligence" cache over past chat answers) ───────────

async def insert_loop(row: dict) -> str:
    """Insert one loop row. Returns its id. Caps live in the row from birth."""
    for key in ("verifier_spec", "last_verdict"):
        value = row.get(key)
        if value is not None and not isinstance(value, str):
            row[key] = json.dumps(value, ensure_ascii=False)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        await db.execute(
            """INSERT INTO loops(
                   id, instance_id, goal, kind, origin, status, turn, max_turns,
                   deadline_at, verifier_spec, last_verdict, task_id,
                   created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["instance_id"],
                row["goal"],
                row.get("kind") or "closed",
                row.get("origin"),
                row.get("status") or "plan",
                int(row.get("turn") or 0),
                int(row.get("max_turns") or 3),
                row.get("deadline_at"),
                row.get("verifier_spec"),
                row.get("last_verdict"),
                row.get("task_id"),
                row["created_at"],
                row["updated_at"],
            ),
        )
        await db.commit()
    return row["id"]


async def get_loop(instance_id: str, loop_id: str) -> "Optional[dict]":
    """One loop row (JSON columns decoded), or ``None``."""
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM loops WHERE instance_id = ? AND id = ?",
            (instance_id, loop_id),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["verifier_spec"] = _pkg()._loads(out.get("verifier_spec"), None)
    out["last_verdict"] = _pkg()._loads(out.get("last_verdict"), None)
    return out


async def list_loops(
    instance_id: str, *, status: "Optional[str]" = None, limit: int = 50
) -> "list[dict]":
    """Loop rows (newest first); optionally filtered by status."""
    sql = "SELECT * FROM loops WHERE instance_id = ?"
    params: list = [instance_id]
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["verifier_spec"] = _pkg()._loads(row.get("verifier_spec"), None)
        row["last_verdict"] = _pkg()._loads(row.get("last_verdict"), None)
    return rows


async def advance_loop(
    instance_id: str,
    loop_id: str,
    *,
    status: "Optional[str]" = None,
    verdict: "Optional[dict]" = None,
    bump_turn: bool = False,
) -> bool:
    """Advance one loop: bump the turn, record the verdict, move the status.

    Returns whether a row changed. The CALLER enforces caps before working;
    this only records state.
    """
    sets, params = ["updated_at = ?"], [_pkg()._now()]
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if verdict is not None:
        sets.append("last_verdict = ?")
        params.append(json.dumps(verdict, ensure_ascii=False))
    if bump_turn:
        sets.append("turn = turn + 1")
    params.extend([instance_id, loop_id])
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE loops SET {', '.join(sets)} WHERE instance_id = ? AND id = ?",
            params,
        )
        await db.commit()
        return bool(cur.rowcount)


# ── bench_runs (LhassaBench results; append-only) ───────────────────────────


