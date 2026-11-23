# SPDX-License-Identifier: AGPL-3.0-or-later
"""Transactional persistence primitives for durable flow execution.

This module deliberately does not execute graphs.  It owns the operational
state machine that a later durable runner will use; ``runs``/``run_steps`` stay
as analytics projections and are not read here.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

from lhassa._vendor import aiosqlite
from lhassa.core import db as core_db


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_RETRY = "waiting_retry"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepAttemptStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class FlowEventKind(str, Enum):
    RUN_CREATED = "RUN_CREATED"
    ORG_BOUND = "ORG_BOUND"
    RUN_CLAIMED = "RUN_CLAIMED"
    RUN_STARTED = "RUN_STARTED"
    CHECKPOINT_SAVED = "CHECKPOINT_SAVED"
    STEP_SCHEDULED = "STEP_SCHEDULED"
    STEP_CLAIMED = "STEP_CLAIMED"
    STEP_STARTED = "STEP_STARTED"
    STEP_HEARTBEAT = "STEP_HEARTBEAT"
    STEP_COMPLETED = "STEP_COMPLETED"
    STEP_FAILED = "STEP_FAILED"
    EDGE_SELECTED = "EDGE_SELECTED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_RESOLVED = "APPROVAL_RESOLVED"
    RUN_PAUSED = "RUN_PAUSED"
    RUN_RESUMED = "RUN_RESUMED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    RUN_CANCELLED = "RUN_CANCELLED"


class RuntimeConflict(RuntimeError):
    """A compare-and-swap, fencing, or uniqueness precondition failed."""


class RunNotFound(KeyError):
    """No run exists in the requested instance scope."""


_TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.WAITING_APPROVAL,
        RunStatus.WAITING_RETRY,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.WAITING_APPROVAL: {
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
    },
    RunStatus.WAITING_RETRY: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: Optional[str], default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


_RUN_JSON_FIELDS = {
    "flow_snapshot": {},
    "inputs": {},
    "frontier": [],
    "visited": [],
    "output": None,
    "org_chain_snapshot": [],
}


def _run_dict(row: Any) -> dict:
    out = dict(row)
    for field, default in _RUN_JSON_FIELDS.items():
        out[field] = _load(out.get(field), default)
    return out


def _event_dict(row: Any) -> dict:
    out = dict(row)
    out["payload"] = _load(out.get("payload"), {})
    return out


class FlowRuntimeRepository:
    """Instance-scoped, transactional access to the durable flow kernel."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        self._initialized_path: Optional[Path] = None

    @property
    def db_path(self) -> Path:
        # Resolve lazily so tests can monkeypatch core_db.DB_PATH after import.
        return self._db_path or Path(core_db.DB_PATH)

    async def initialize(self) -> None:
        # Memoized per (repository, resolved path): the schema check/migration
        # pass takes a BEGIN IMMEDIATE write lock, so re-running it on every
        # repository call would serialize hot paths (per-node checkpoints, the
        # lease-renewal loop). Keyed on the path so a monkeypatched DB_PATH in
        # tests still triggers a fresh init. Idempotent, so a concurrent double
        # run is harmless.
        path = self.db_path
        if self._initialized_path == path:
            return
        if self._db_path is None or path == Path(core_db.DB_PATH):
            await core_db.init_db()
            self._initialized_path = path
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as conn:
            core_db.ensure_supported(
                await core_db.get_schema_version(conn), core_db.SCHEMA_VERSION
            )
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await core_db.apply_migrations(conn, core_db.MIGRATIONS)
        self._initialized_path = path

    async def _connection(self):
        await self.initialize()
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        return conn

    async def _insert_event(
        self,
        conn,
        *,
        instance_id: str,
        run_id: str,
        kind: FlowEventKind,
        payload: Optional[dict] = None,
        step_attempt_id: Optional[str] = None,
        actor_id: str = "",
        actor_role: str = "",
        dedupe_key: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> int:
        cur = await conn.execute(
            """INSERT INTO flow_events(
                   instance_id, run_id, step_attempt_id, kind, payload,
                   schema_version, actor_id, actor_role, dedupe_key, created_at)
               VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (
                instance_id,
                run_id,
                step_attempt_id,
                kind.value,
                _dump(payload or {}),
                actor_id,
                actor_role,
                dedupe_key,
                created_at or _iso(_now()),
            ),
        )
        return int(cur.lastrowid)

    async def create_run(
        self,
        instance_id: str,
        flow_id: str,
        flow_snapshot: dict,
        *,
        inputs: Optional[dict] = None,
        run_id: Optional[str] = None,
        flow_digest: str = "",
        requested_by: str = "",
        owner_department: str = "",
        org_chain_snapshot: Optional[list] = None,
        topology_digest: str = "",
        idempotency_key: Optional[str] = None,
        frontier: Optional[list] = None,
        created_at: Optional[datetime] = None,
    ) -> dict:
        """Create a queued run, returning the existing run for a repeated key."""
        if not instance_id or not flow_id:
            raise ValueError("instance_id and flow_id are required")
        key = idempotency_key or None
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        timestamp = _iso(created_at or _now())
        conn = await self._connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            if key is not None:
                async with conn.execute(
                    """SELECT * FROM flow_run_state
                       WHERE instance_id = ? AND flow_id = ? AND idempotency_key = ?""",
                    (instance_id, flow_id, key),
                ) as cur:
                    existing = await cur.fetchone()
                if existing is not None:
                    await conn.commit()
                    return _run_dict(existing)

            await conn.execute(
                """INSERT INTO flow_run_state(
                       run_id, instance_id, flow_id, flow_digest, flow_snapshot,
                       status, inputs, frontier, visited, requested_by,
                       owner_department, org_chain_snapshot, topology_digest,
                       idempotency_key, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, 'queued', ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    instance_id,
                    flow_id,
                    flow_digest,
                    _dump(flow_snapshot),
                    _dump(inputs or {}),
                    _dump(frontier or []),
                    requested_by,
                    owner_department,
                    _dump(org_chain_snapshot or []),
                    topology_digest,
                    key,
                    timestamp,
                    timestamp,
                ),
            )
            await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=run_id,
                kind=FlowEventKind.RUN_CREATED,
                payload={"flow_id": flow_id, "status": RunStatus.QUEUED.value},
                actor_id=requested_by,
                dedupe_key=f"run-created:{run_id}",
                created_at=timestamp,
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        result = await self.get_run(instance_id, run_id)
        if result is None:
            raise RuntimeError(f"run {run_id} vanished after commit")
        return result

    async def get_run(self, instance_id: str, run_id: str) -> Optional[dict]:
        conn = await self._connection()
        try:
            async with conn.execute(
                "SELECT * FROM flow_run_state WHERE instance_id = ? AND run_id = ?",
                (instance_id, run_id),
            ) as cur:
                row = await cur.fetchone()
            return _run_dict(row) if row is not None else None
        finally:
            await conn.close()

    async def list_runs(
        self,
        instance_id: str,
        *,
        statuses: Optional[Iterable[RunStatus | str]] = None,
        limit: int = 200,
    ) -> list[dict]:
        sql = "SELECT * FROM flow_run_state WHERE instance_id = ?"
        args: list[Any] = [instance_id]
        values = [RunStatus(s).value for s in statuses or []]
        if values:
            sql += f" AND status IN ({','.join('?' for _ in values)})"
            args.extend(values)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        conn = await self._connection()
        try:
            async with conn.execute(sql, args) as cur:
                return [_run_dict(row) for row in await cur.fetchall()]
        finally:
            await conn.close()

    @staticmethod
    def _require_fence(row: Any, lease_token: Optional[str]) -> None:
        current = row["lease_token"]
        if current is not None and lease_token != current:
            raise RuntimeConflict("stale or missing lease token")

    async def transition_run(
        self,
        instance_id: str,
        run_id: str,
        *,
        expected_status: RunStatus | str,
        new_status: RunStatus | str,
        event_kind: FlowEventKind | str,
        payload: Optional[dict] = None,
        expected_version: Optional[int] = None,
        lease_token: Optional[str] = None,
        output: Any = None,
        error: Optional[str] = None,
        next_wakeup_at: Optional[datetime] = None,
        actor_id: str = "",
        actor_role: str = "",
    ) -> dict:
        """CAS a run state and append its event in the same transaction."""
        expected = RunStatus(expected_status)
        target = RunStatus(new_status)
        kind = FlowEventKind(event_kind)
        if target not in _TRANSITIONS[expected]:
            raise ValueError(f"invalid run transition {expected.value} -> {target.value}")
        timestamp = _iso(_now())
        conn = await self._connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT * FROM flow_run_state WHERE instance_id = ? AND run_id = ?",
                (instance_id, run_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise RunNotFound(run_id)
            if row["status"] != expected.value:
                raise RuntimeConflict(
                    f"expected status {expected.value}, found {row['status']}"
                )
            if expected_version is not None and row["state_version"] != expected_version:
                raise RuntimeConflict(
                    f"expected version {expected_version}, found {row['state_version']}"
                )
            self._require_fence(row, lease_token)
            clear_lease = target != RunStatus.RUNNING
            await conn.execute(
                """UPDATE flow_run_state
                   SET status = ?, state_version = state_version + 1,
                       output = COALESCE(?, output), error = ?, next_wakeup_at = ?,
                       lease_owner = CASE WHEN ? THEN NULL ELSE lease_owner END,
                       lease_token = CASE WHEN ? THEN NULL ELSE lease_token END,
                       lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END,
                       heartbeat_at = CASE WHEN ? THEN NULL ELSE heartbeat_at END,
                       updated_at = ?
                   WHERE instance_id = ? AND run_id = ?
                     AND status = ? AND state_version = ?""",
                (
                    target.value,
                    _dump(output) if output is not None else None,
                    error,
                    _iso(next_wakeup_at) if next_wakeup_at else None,
                    clear_lease,
                    clear_lease,
                    clear_lease,
                    clear_lease,
                    timestamp,
                    instance_id,
                    run_id,
                    expected.value,
                    row["state_version"],
                ),
            )
            await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=run_id,
                kind=kind,
                payload=payload or {"from": expected.value, "to": target.value},
                actor_id=actor_id,
                actor_role=actor_role,
                created_at=timestamp,
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        result = await self.get_run(instance_id, run_id)
        if result is None:
            raise RuntimeError(f"run {run_id} vanished after commit")
        return result

    async def append_checkpoint(
        self,
        instance_id: str,
        run_id: str,
        *,
        frontier: list,
        visited: list,
        state: dict,
        expected_version: Optional[int] = None,
        lease_token: Optional[str] = None,
        actor_id: str = "",
        actor_role: str = "",
    ) -> dict:
        """Persist a checkpoint, materialized cursor, and event atomically."""
        timestamp = _iso(_now())
        conn = await self._connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT * FROM flow_run_state WHERE instance_id = ? AND run_id = ?",
                (instance_id, run_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise RunNotFound(run_id)
            if RunStatus(row["status"]) in _TERMINAL:
                raise RuntimeConflict("cannot checkpoint a terminal run")
            if expected_version is not None and row["state_version"] != expected_version:
                raise RuntimeConflict(
                    f"expected version {expected_version}, found {row['state_version']}"
                )
            self._require_fence(row, lease_token)
            seq = int(row["checkpoint_seq"]) + 1
            await conn.execute(
                """INSERT INTO flow_checkpoints(
                       run_id, seq, instance_id, frontier, visited, state,
                       schema_version, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    run_id,
                    seq,
                    instance_id,
                    _dump(frontier),
                    _dump(visited),
                    _dump(state),
                    timestamp,
                ),
            )
            await conn.execute(
                """UPDATE flow_run_state
                   SET frontier = ?, visited = ?, checkpoint_seq = ?,
                       state_version = state_version + 1, updated_at = ?
                   WHERE instance_id = ? AND run_id = ? AND state_version = ?""",
                (
                    _dump(frontier),
                    _dump(visited),
                    seq,
                    timestamp,
                    instance_id,
                    run_id,
                    row["state_version"],
                ),
            )
            event_id = await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=run_id,
                kind=FlowEventKind.CHECKPOINT_SAVED,
                payload={"seq": seq},
                actor_id=actor_id,
                actor_role=actor_role,
                dedupe_key=f"checkpoint:{run_id}:{seq}",
                created_at=timestamp,
            )
            await conn.execute(
                """UPDATE flow_checkpoints SET source_event_id = ?
                   WHERE run_id = ? AND seq = ?""",
                (event_id, run_id, seq),
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        return await self.get_checkpoint(instance_id, run_id, seq)

    async def get_checkpoint(
        self, instance_id: str, run_id: str, seq: Optional[int] = None
    ) -> Optional[dict]:
        sql = """SELECT * FROM flow_checkpoints
                 WHERE instance_id = ? AND run_id = ?"""
        args: list[Any] = [instance_id, run_id]
        if seq is None:
            sql += " ORDER BY seq DESC LIMIT 1"
        else:
            sql += " AND seq = ?"
            args.append(seq)
        conn = await self._connection()
        try:
            async with conn.execute(sql, args) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            out = dict(row)
            for field, default in (("frontier", []), ("visited", []), ("state", {})):
                out[field] = _load(out.get(field), default)
            return out
        finally:
            await conn.close()

    async def list_events(self, instance_id: str, run_id: str) -> list[dict]:
        conn = await self._connection()
        try:
            async with conn.execute(
                """SELECT * FROM flow_events
                   WHERE instance_id = ? AND run_id = ? ORDER BY id""",
                (instance_id, run_id),
            ) as cur:
                return [_event_dict(row) for row in await cur.fetchall()]
        finally:
            await conn.close()

    async def park_gate(
        self,
        instance_id: str,
        run_id: str,
        *,
        frontier: list,
        visited: list,
        state: dict,
        approval_id: str,
        approval_payload: dict,
        expected_version: int,
        lease_token: str,
    ) -> dict:
        """Commit approval, checkpoint, and sleeping run state atomically."""
        timestamp = _iso(_now())
        conn = await self._connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT * FROM flow_run_state WHERE instance_id = ? AND run_id = ?",
                (instance_id, run_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise RunNotFound(run_id)
            if row["status"] != RunStatus.RUNNING.value:
                raise RuntimeConflict(f"run {run_id} is not running")
            if row["state_version"] != expected_version:
                raise RuntimeConflict(
                    f"expected version {expected_version}, found {row['state_version']}"
                )
            self._require_fence(row, lease_token)
            pending = state.get("pending_approval") or {}
            if (
                not approval_id
                or pending.get("approval_id") != approval_id
                or not approval_payload.get("node_id")
            ):
                raise RuntimeConflict("approval cursor is incomplete")

            await conn.execute(
                """INSERT INTO approvals(
                       id, instance_id, tipo, payload, estado, creado_en)
                   VALUES(?, ?, 'flow_gate', ?, 'pendiente', ?)""",
                (approval_id, instance_id, _dump(approval_payload), timestamp),
            )
            seq = int(row["checkpoint_seq"]) + 1
            await conn.execute(
                """INSERT INTO flow_checkpoints(
                       run_id, seq, instance_id, frontier, visited, state,
                       schema_version, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    run_id,
                    seq,
                    instance_id,
                    _dump(frontier),
                    _dump(visited),
                    _dump(state),
                    timestamp,
                ),
            )
            checkpoint_event_id = await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=run_id,
                kind=FlowEventKind.CHECKPOINT_SAVED,
                payload={"seq": seq, "reason": "approval_requested"},
                dedupe_key=f"checkpoint:{run_id}:{seq}",
                created_at=timestamp,
            )
            await conn.execute(
                """UPDATE flow_checkpoints SET source_event_id = ?
                   WHERE run_id = ? AND seq = ?""",
                (checkpoint_event_id, run_id, seq),
            )
            await conn.execute(
                """UPDATE flow_run_state
                   SET status = 'waiting_approval', frontier = ?, visited = ?,
                       checkpoint_seq = ?, state_version = state_version + 1,
                       lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, heartbeat_at = NULL, updated_at = ?
                   WHERE instance_id = ? AND run_id = ?
                     AND status = 'running' AND state_version = ?""",
                (
                    _dump(frontier),
                    _dump(visited),
                    seq,
                    timestamp,
                    instance_id,
                    run_id,
                    expected_version,
                ),
            )
            await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=run_id,
                kind=FlowEventKind.APPROVAL_REQUESTED,
                payload={
                    "approval_id": approval_id,
                    "node_id": approval_payload["node_id"],
                },
                dedupe_key=f"approval-requested:{approval_id}",
                created_at=timestamp,
            )
            await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=run_id,
                kind=FlowEventKind.RUN_PAUSED,
                payload={"reason": "approval", "approval_id": approval_id},
                dedupe_key=f"run-paused:{run_id}:{seq}",
                created_at=timestamp,
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        run = await self.get_run(instance_id, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} vanished after commit")
        return run

    async def park_retry(
        self,
        instance_id: str,
        run_id: str,
        *,
        frontier: list,
        visited: list,
        state: dict,
        next_wakeup_at: datetime,
        expected_version: int,
        lease_token: str,
    ) -> dict:
        """Commit checkpoint + WAITING_RETRY run state atomically.

        Mirrors :meth:`park_gate`'s shape (a running claim pauses, atomically,
        with its cursor persisted) but for a retryable node failure instead of
        an approval: no ``approvals`` row, and the run becomes claimable again
        the moment ``next_wakeup_at`` is due; :meth:`claim_run` already knows
        how to pick a ``waiting_retry`` row back up. The lease is released
        exactly like a completed run (the CAS/lease/fencing model is unchanged).
        """
        timestamp = _iso(_now())
        conn = await self._connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT * FROM flow_run_state WHERE instance_id = ? AND run_id = ?",
                (instance_id, run_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise RunNotFound(run_id)
            if row["status"] != RunStatus.RUNNING.value:
                raise RuntimeConflict(f"run {run_id} is not running")
            if row["state_version"] != expected_version:
                raise RuntimeConflict(
                    f"expected version {expected_version}, found {row['state_version']}"
                )
            self._require_fence(row, lease_token)

            seq = int(row["checkpoint_seq"]) + 1
            await conn.execute(
                """INSERT INTO flow_checkpoints(
                       run_id, seq, instance_id, frontier, visited, state,
                       schema_version, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    run_id,
                    seq,
                    instance_id,
                    _dump(frontier),
                    _dump(visited),
                    _dump(state),
                    timestamp,
                ),
            )
            checkpoint_event_id = await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=run_id,
                kind=FlowEventKind.CHECKPOINT_SAVED,
                payload={"seq": seq, "reason": "retry_scheduled"},
                dedupe_key=f"checkpoint:{run_id}:{seq}",
                created_at=timestamp,
            )
            await conn.execute(
                """UPDATE flow_checkpoints SET source_event_id = ?
                   WHERE run_id = ? AND seq = ?""",
                (checkpoint_event_id, run_id, seq),
            )
            await conn.execute(
                """UPDATE flow_run_state
                   SET status = 'waiting_retry', frontier = ?, visited = ?,
                       checkpoint_seq = ?, state_version = state_version + 1,
                       lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, heartbeat_at = NULL,
                       next_wakeup_at = ?, updated_at = ?
                   WHERE instance_id = ? AND run_id = ?
                     AND status = 'running' AND state_version = ?""",
                (
                    _dump(frontier),
                    _dump(visited),
                    seq,
                    _iso(next_wakeup_at),
                    timestamp,
                    instance_id,
                    run_id,
                    expected_version,
                ),
            )
            await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=run_id,
                kind=FlowEventKind.RETRY_SCHEDULED,
                payload={"next_wakeup_at": _iso(next_wakeup_at)},
                dedupe_key=f"retry-scheduled:{run_id}:{seq}",
                created_at=timestamp,
            )
            await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=run_id,
                kind=FlowEventKind.RUN_PAUSED,
                payload={"reason": "retry", "next_wakeup_at": _iso(next_wakeup_at)},
                dedupe_key=f"run-paused:{run_id}:{seq}",
                created_at=timestamp,
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        run = await self.get_run(instance_id, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} vanished after commit")
        return run

    async def heartbeat_run(
        self,
        instance_id: str,
        run_id: str,
        *,
        lease_token: str,
        lease_seconds: int = 60,
    ) -> None:
        """Renew a running lease without changing the cursor CAS version."""
        current = _now()
        conn = await self._connection()
        try:
            cur = await conn.execute(
                """UPDATE flow_run_state
                   SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                   WHERE instance_id = ? AND run_id = ?
                     AND status = 'running' AND lease_token = ?""",
                (
                    _iso(current),
                    _iso(current + timedelta(seconds=max(1, int(lease_seconds)))),
                    _iso(current),
                    instance_id,
                    run_id,
                    lease_token,
                ),
            )
            await conn.commit()
            if cur.rowcount != 1:
                raise RuntimeConflict("stale lease token")
        finally:
            await conn.close()

    async def resolve_gate_approval(
        self,
        instance_id: str,
        approval_id: str,
        *,
        approved: bool,
        actor_id: str,
    ) -> dict:
        """Resolve a flow gate and update its run cursor in one transaction.

        Approval writes and run state must not be separate commits: otherwise a
        crash can leave a resolved queue item attached to a permanently sleeping
        run. Approved gates are made claimable with their decision embedded in a
        new checkpoint; rejected gates terminate the run without executing any
        downstream node.
        """
        timestamp = _iso(_now())
        conn = await self._connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT * FROM approvals WHERE instance_id = ? AND id = ?",
                (instance_id, approval_id),
            ) as cur:
                approval_row = await cur.fetchone()
            if approval_row is None:
                raise RunNotFound(approval_id)
            if approval_row["estado"] != "pendiente":
                raise RuntimeConflict(
                    f"approval {approval_id} is already {approval_row['estado']}"
                )
            if approval_row["tipo"] != "flow_gate":
                raise ValueError(f"approval {approval_id} is not a flow gate")

            approval_payload = _load(approval_row["payload"], {})
            run_id = str(approval_payload.get("run_id") or "")
            node_id = str(
                approval_payload.get("node_id")
                or approval_payload.get("node")
                or ""
            )
            if not run_id or not node_id:
                raise RuntimeConflict("flow approval is missing run or node identity")

            async with conn.execute(
                "SELECT * FROM flow_run_state WHERE instance_id = ? AND run_id = ?",
                (instance_id, run_id),
            ) as cur:
                run_row = await cur.fetchone()
            if run_row is None:
                raise RunNotFound(run_id)
            if run_row["status"] != RunStatus.WAITING_APPROVAL.value:
                raise RuntimeConflict(
                    f"run {run_id} is {run_row['status']}, not waiting_approval"
                )

            async with conn.execute(
                """SELECT * FROM flow_checkpoints
                   WHERE instance_id = ? AND run_id = ?
                   ORDER BY seq DESC LIMIT 1""",
                (instance_id, run_id),
            ) as cur:
                checkpoint_row = await cur.fetchone()
            if checkpoint_row is None:
                raise RuntimeConflict("flow approval has no resumable checkpoint")
            state = _load(checkpoint_row["state"], {})
            pending = state.get("pending_approval") or {}
            if (
                pending.get("approval_id") != approval_id
                or pending.get("node_id") != node_id
            ):
                raise RuntimeConflict("flow approval does not match the current cursor")

            decision = "aprobada" if approved else "rechazada"
            cur = await conn.execute(
                """UPDATE approvals SET estado = ?, resuelto_en = ?
                   WHERE instance_id = ? AND id = ? AND estado = 'pendiente'""",
                (decision, timestamp, instance_id, approval_id),
            )
            if cur.rowcount != 1:
                raise RuntimeConflict(f"approval {approval_id} was resolved concurrently")

            await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=run_id,
                kind=FlowEventKind.APPROVAL_RESOLVED,
                payload={
                    "approval_id": approval_id,
                    "node_id": node_id,
                    "approved": approved,
                },
                actor_id=actor_id,
                dedupe_key=f"approval-resolved:{approval_id}",
                created_at=timestamp,
            )

            if approved:
                decisions = dict(state.get("approval_decisions") or {})
                decisions[node_id] = True
                state["approval_decisions"] = decisions
                seq = int(run_row["checkpoint_seq"]) + 1
                await conn.execute(
                    """INSERT INTO flow_checkpoints(
                           run_id, seq, instance_id, frontier, visited, state,
                           schema_version, created_at)
                       VALUES(?, ?, ?, ?, ?, ?, 1, ?)""",
                    (
                        run_id,
                        seq,
                        instance_id,
                        checkpoint_row["frontier"],
                        checkpoint_row["visited"],
                        _dump(state),
                        timestamp,
                    ),
                )
                checkpoint_event_id = await self._insert_event(
                    conn,
                    instance_id=instance_id,
                    run_id=run_id,
                    kind=FlowEventKind.CHECKPOINT_SAVED,
                    payload={"seq": seq, "reason": "approval_resolved"},
                    actor_id=actor_id,
                    dedupe_key=f"checkpoint:{run_id}:{seq}",
                    created_at=timestamp,
                )
                await conn.execute(
                    """UPDATE flow_checkpoints SET source_event_id = ?
                       WHERE run_id = ? AND seq = ?""",
                    (checkpoint_event_id, run_id, seq),
                )
                await conn.execute(
                    """UPDATE flow_run_state
                       SET status = 'queued', checkpoint_seq = ?,
                           state_version = state_version + 1,
                           lease_owner = NULL, lease_token = NULL,
                           lease_expires_at = NULL, heartbeat_at = NULL,
                           updated_at = ?
                       WHERE instance_id = ? AND run_id = ?
                         AND status = 'waiting_approval' AND state_version = ?""",
                    (
                        seq,
                        timestamp,
                        instance_id,
                        run_id,
                        run_row["state_version"],
                    ),
                )
                await self._insert_event(
                    conn,
                    instance_id=instance_id,
                    run_id=run_id,
                    kind=FlowEventKind.RUN_RESUMED,
                    payload={"approval_id": approval_id, "status": "queued"},
                    actor_id=actor_id,
                    dedupe_key=f"run-resumed:{run_id}:{seq}",
                    created_at=timestamp,
                )
            else:
                await conn.execute(
                    """UPDATE flow_run_state
                       SET status = 'cancelled',
                           state_version = state_version + 1,
                           error = 'approval rejected', updated_at = ?
                       WHERE instance_id = ? AND run_id = ?
                         AND status = 'waiting_approval' AND state_version = ?""",
                    (
                        timestamp,
                        instance_id,
                        run_id,
                        run_row["state_version"],
                    ),
                )
                await self._insert_event(
                    conn,
                    instance_id=instance_id,
                    run_id=run_id,
                    kind=FlowEventKind.RUN_CANCELLED,
                    payload={"reason": "approval_rejected", "node_id": node_id},
                    actor_id=actor_id,
                    dedupe_key=f"run-cancelled:{run_id}",
                    created_at=timestamp,
                )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()

        run = await self.get_run(instance_id, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} vanished after commit")
        checkpoint = await self.get_checkpoint(instance_id, run_id)
        return {
            "approval_id": approval_id,
            "approved": approved,
            "run": run,
            "checkpoint": checkpoint,
        }

    async def claim_run(
        self,
        instance_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        run_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[dict]:
        """Atomically claim one eligible run and return its fresh fencing token."""
        if not worker_id:
            raise ValueError("worker_id is required")
        current = now or _now()
        now_iso = _iso(current)
        expires = _iso(current + timedelta(seconds=max(1, int(lease_seconds))))
        token = uuid.uuid4().hex
        conn = await self._connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            sql = """SELECT * FROM flow_run_state
                     WHERE instance_id = ?
                       AND (
                         (status = 'queued' AND (next_wakeup_at IS NULL OR next_wakeup_at <= ?))
                         OR (status = 'waiting_retry' AND next_wakeup_at IS NOT NULL
                             AND next_wakeup_at <= ?)
                         OR (status = 'running' AND lease_expires_at IS NOT NULL
                             AND lease_expires_at <= ?)
                       )"""
            args: list[Any] = [instance_id, now_iso, now_iso, now_iso]
            if run_id is not None:
                sql += " AND run_id = ?"
                args.append(run_id)
            sql += " ORDER BY created_at, rowid LIMIT 1"
            async with conn.execute(sql, args) as cur:
                row = await cur.fetchone()
            if row is None:
                await conn.commit()
                return None
            if row["status"] == RunStatus.RUNNING.value:
                await self._insert_event(
                    conn,
                    instance_id=instance_id,
                    run_id=row["run_id"],
                    kind=FlowEventKind.LEASE_EXPIRED,
                    payload={"lease_owner": row["lease_owner"]},
                    created_at=now_iso,
                )
            await conn.execute(
                """UPDATE flow_run_state
                   SET status = 'running', lease_owner = ?, lease_token = ?,
                       lease_expires_at = ?, heartbeat_at = ?, next_wakeup_at = NULL,
                       state_version = state_version + 1, updated_at = ?
                   WHERE instance_id = ? AND run_id = ? AND state_version = ?""",
                (
                    worker_id,
                    token,
                    expires,
                    now_iso,
                    now_iso,
                    instance_id,
                    row["run_id"],
                    row["state_version"],
                ),
            )
            await self._insert_event(
                conn,
                instance_id=instance_id,
                run_id=row["run_id"],
                kind=FlowEventKind.RUN_CLAIMED,
                payload={"worker_id": worker_id, "lease_expires_at": expires},
                actor_id=worker_id,
                created_at=now_iso,
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        return await self.get_run(instance_id, row["run_id"])


__all__ = [
    "FlowEventKind",
    "FlowRuntimeRepository",
    "RunNotFound",
    "RunStatus",
    "RuntimeConflict",
    "StepAttemptStatus",
]
