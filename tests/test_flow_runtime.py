"""Operational invariants for the durable flow runtime repository."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import lhassa.core.db as core_db
from lhassa._vendor import aiosqlite
from lhassa.flows.runtime import (
    FlowEventKind,
    FlowRuntimeRepository,
    RunStatus,
    RuntimeConflict,
)


async def _repo(tmp_path, monkeypatch) -> FlowRuntimeRepository:
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return FlowRuntimeRepository()


def _flow(flow_id: str = "flow-a") -> dict:
    return {"id": flow_id, "name": flow_id, "nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_fresh_schema_has_durable_flow_tables(tmp_path, monkeypatch):
    repo = await _repo(tmp_path, monkeypatch)
    async with aiosqlite.connect(repo.db_path) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
        async with conn.execute("PRAGMA user_version") as cur:
            (version,) = await cur.fetchone()

    assert {
        "flow_run_state",
        "flow_step_attempts",
        "flow_checkpoints",
        "flow_events",
    }.issubset(tables)
    assert version == core_db.SCHEMA_VERSION


@pytest.mark.asyncio
async def test_v1_upgrade_adds_kernel_without_touching_analytics(tmp_path, monkeypatch):
    import lhassa.core.db as db

    path = tmp_path / "brain.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    async with aiosqlite.connect(path) as conn:
        await db.apply_migrations(conn, db.MIGRATIONS[:1])
        await conn.execute(
            """INSERT INTO runs(
                   id, instance_id, kind, status, started_at, raw)
               VALUES('legacy-run', 'inst', 'flow', 'completed', '2026-01-01', '{}')"""
        )
        await conn.commit()

    await db.init_db()

    async with aiosqlite.connect(path) as conn:
        async with conn.execute("PRAGMA user_version") as cur:
            assert await cur.fetchone() == (db.SCHEMA_VERSION,)
        async with conn.execute("SELECT id FROM runs") as cur:
            assert await cur.fetchone() == ("legacy-run",)
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='flow_events'"
        ) as cur:
            assert await cur.fetchone() == ("flow_events",)


@pytest.mark.asyncio
async def test_create_get_and_list_are_instance_scoped(tmp_path, monkeypatch):
    repo = await _repo(tmp_path, monkeypatch)
    run_a = await repo.create_run("inst-a", "flow", _flow("flow"), run_id="run-a")
    await repo.create_run("inst-b", "flow", _flow("flow"), run_id="run-b")

    assert run_a["flow_snapshot"] == _flow("flow")
    assert await repo.get_run("inst-b", "run-a") is None
    assert [row["run_id"] for row in await repo.list_runs("inst-a")] == ["run-a"]
    assert [event["kind"] for event in await repo.list_events("inst-a", "run-a")] == [
        FlowEventKind.RUN_CREATED.value
    ]
    assert await repo.list_events("inst-b", "run-a") == []


@pytest.mark.asyncio
async def test_create_is_idempotent_per_instance_flow_and_key(tmp_path, monkeypatch):
    repo = await _repo(tmp_path, monkeypatch)
    first = await repo.create_run(
        "inst", "flow", _flow("flow"), run_id="original", idempotency_key="daily"
    )
    repeated = await repo.create_run(
        "inst", "flow", {"changed": True}, run_id="duplicate", idempotency_key="daily"
    )

    assert repeated["run_id"] == first["run_id"] == "original"
    assert len(await repo.list_runs("inst")) == 1
    assert len(await repo.list_events("inst", "original")) == 1

    other_flow = await repo.create_run(
        "inst", "other", _flow("other"), idempotency_key="daily"
    )
    other_instance = await repo.create_run(
        "other-inst", "flow", _flow("flow"), idempotency_key="daily"
    )
    assert other_flow["run_id"] != first["run_id"]
    assert other_instance["run_id"] != first["run_id"]


@pytest.mark.asyncio
async def test_transition_and_event_roll_back_together(tmp_path, monkeypatch):
    repo = await _repo(tmp_path, monkeypatch)
    await repo.create_run("inst", "flow", _flow("flow"), run_id="run")

    async def fail_event(*_args, **_kwargs):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(repo, "_insert_event", fail_event)
    with pytest.raises(RuntimeError, match="event write failed"):
        await repo.transition_run(
            "inst",
            "run",
            expected_status=RunStatus.QUEUED,
            new_status=RunStatus.RUNNING,
            event_kind=FlowEventKind.RUN_STARTED,
        )

    row = await repo.get_run("inst", "run")
    assert row is not None
    assert row["status"] == RunStatus.QUEUED.value
    assert row["state_version"] == 0


@pytest.mark.asyncio
async def test_checkpoint_state_and_event_are_atomic(tmp_path, monkeypatch):
    repo = await _repo(tmp_path, monkeypatch)
    await repo.create_run("inst", "flow", _flow("flow"), run_id="run")
    claimed = await repo.claim_run("inst", "worker", run_id="run")
    assert claimed is not None

    checkpoint = await repo.append_checkpoint(
        "inst",
        "run",
        frontier=[{"node_id": "task", "data": {"x": 1}}],
        visited=["trigger"],
        state={"last": "trigger"},
        expected_version=claimed["state_version"],
        lease_token=claimed["lease_token"],
    )
    assert checkpoint["seq"] == 1
    assert checkpoint["source_event_id"] is not None
    row = await repo.get_run("inst", "run")
    assert row is not None
    assert row["checkpoint_seq"] == 1
    assert row["frontier"][0]["node_id"] == "task"

    before_events = await repo.list_events("inst", "run")

    async def fail_event(*_args, **_kwargs):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(repo, "_insert_event", fail_event)
    with pytest.raises(RuntimeError, match="event write failed"):
        await repo.append_checkpoint(
            "inst",
            "run",
            frontier=[],
            visited=["trigger", "task"],
            state={"last": "task"},
            expected_version=row["state_version"],
            lease_token=row["lease_token"],
        )

    after = await repo.get_run("inst", "run")
    assert after is not None
    assert after["checkpoint_seq"] == 1
    assert await repo.get_checkpoint("inst", "run", 2) is None
    assert await repo.list_events("inst", "run") == before_events


@pytest.mark.asyncio
async def test_concurrent_claims_have_one_winner(tmp_path, monkeypatch):
    repo = await _repo(tmp_path, monkeypatch)
    await repo.create_run("inst", "flow", _flow("flow"), run_id="run")
    repo_a = FlowRuntimeRepository()
    repo_b = FlowRuntimeRepository()

    claims = await asyncio.gather(
        repo_a.claim_run("inst", "worker-a", run_id="run"),
        repo_b.claim_run("inst", "worker-b", run_id="run"),
    )

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0]["lease_owner"] in {"worker-a", "worker-b"}
    assert winners[0]["lease_token"]
    events = await repo.list_events("inst", "run")
    assert [event["kind"] for event in events].count(
        FlowEventKind.RUN_CLAIMED.value
    ) == 1


@pytest.mark.asyncio
async def test_reclaim_fences_stale_worker_token(tmp_path, monkeypatch):
    repo = await _repo(tmp_path, monkeypatch)
    await repo.create_run("inst", "flow", _flow("flow"), run_id="run")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = await repo.claim_run(
        "inst", "worker-a", run_id="run", lease_seconds=10, now=start
    )
    assert first is not None
    second = await repo.claim_run(
        "inst",
        "worker-b",
        run_id="run",
        lease_seconds=10,
        now=start + timedelta(seconds=11),
    )
    assert second is not None
    assert second["lease_token"] != first["lease_token"]

    with pytest.raises(RuntimeConflict, match="stale"):
        await repo.transition_run(
            "inst",
            "run",
            expected_status=RunStatus.RUNNING,
            new_status=RunStatus.COMPLETED,
            event_kind=FlowEventKind.RUN_COMPLETED,
            lease_token=first["lease_token"],
        )

    completed = await repo.transition_run(
        "inst",
        "run",
        expected_status=RunStatus.RUNNING,
        new_status=RunStatus.COMPLETED,
        event_kind=FlowEventKind.RUN_COMPLETED,
        lease_token=second["lease_token"],
    )
    assert completed["status"] == RunStatus.COMPLETED.value
    assert completed["lease_token"] is None
    kinds = [event["kind"] for event in await repo.list_events("inst", "run")]
    assert FlowEventKind.LEASE_EXPIRED.value in kinds


async def _waiting_gate(repo, *, run_id: str, approval_id: str):
    from lhassa.core import db

    await repo.create_run("inst", "flow", _flow("flow"), run_id=run_id)
    claimed = await repo.claim_run("inst", "worker", run_id=run_id)
    assert claimed is not None
    checkpoint = await repo.append_checkpoint(
        "inst",
        run_id,
        frontier=[{"node_id": "gate", "data": {"draft": "text"}}],
        visited=["trigger"],
        state={
            "steps": [],
            "approval_decisions": {},
            "pending_approval": {
                "approval_id": approval_id,
                "node_id": "gate",
            },
        },
        expected_version=claimed["state_version"],
        lease_token=claimed["lease_token"],
    )
    row = await repo.get_run("inst", run_id)
    assert row is not None
    await repo.transition_run(
        "inst",
        run_id,
        expected_status=RunStatus.RUNNING,
        new_status=RunStatus.WAITING_APPROVAL,
        event_kind=FlowEventKind.APPROVAL_REQUESTED,
        expected_version=row["state_version"],
        lease_token=row["lease_token"],
        payload={"approval_id": approval_id, "node_id": "gate"},
    )
    await db.add_approval(
        "inst",
        "flow_gate",
        {"run_id": run_id, "flow_id": "flow", "node_id": "gate"},
        approval_id=approval_id,
    )
    return checkpoint


@pytest.mark.asyncio
async def test_approve_gate_is_atomic_and_embeds_resume_decision(tmp_path, monkeypatch):
    from lhassa.core import db

    repo = await _repo(tmp_path, monkeypatch)
    await _waiting_gate(repo, run_id="run", approval_id="ap-flow")

    result = await repo.resolve_gate_approval(
        "inst", "ap-flow", approved=True, actor_id="owner"
    )

    assert result["run"]["status"] == RunStatus.QUEUED.value
    assert result["checkpoint"]["seq"] == 2
    state = result["checkpoint"]["state"]
    assert state["approval_decisions"] == {"gate": True}
    assert state["pending_approval"]["approval_id"] == "ap-flow"
    approval = await db.get_approval("inst", "ap-flow")
    assert approval is not None and approval["estado"] == "aprobada"
    kinds = [event["kind"] for event in await repo.list_events("inst", "run")]
    assert kinds.count(FlowEventKind.APPROVAL_RESOLVED.value) == 1

    with pytest.raises(RuntimeConflict, match="already aprobada"):
        await repo.resolve_gate_approval(
            "inst", "ap-flow", approved=True, actor_id="owner"
        )


@pytest.mark.asyncio
async def test_reject_gate_cancels_without_moving_cursor(tmp_path, monkeypatch):
    from lhassa.core import db

    repo = await _repo(tmp_path, monkeypatch)
    await _waiting_gate(repo, run_id="run", approval_id="ap-flow")

    result = await repo.resolve_gate_approval(
        "inst", "ap-flow", approved=False, actor_id="owner"
    )

    assert result["run"]["status"] == RunStatus.CANCELLED.value
    assert result["run"]["checkpoint_seq"] == 1
    approval = await db.get_approval("inst", "ap-flow")
    assert approval is not None and approval["estado"] == "rechazada"
    kinds = [event["kind"] for event in await repo.list_events("inst", "run")]
    assert FlowEventKind.RUN_CANCELLED.value in kinds


@pytest.mark.asyncio
async def test_gate_parking_rolls_back_approval_checkpoint_and_pause(
    tmp_path, monkeypatch
):
    from lhassa.core import db

    repo = await _repo(tmp_path, monkeypatch)
    await repo.create_run("inst", "flow", _flow("flow"), run_id="run")
    claimed = await repo.claim_run("inst", "worker", run_id="run")
    assert claimed is not None
    state = {
        "steps": [],
        "pending_approval": {
            "approval_id": "ap-flow",
            "node_id": "gate",
            "approval_payload": {
                "run_id": "run",
                "flow_id": "flow",
                "node_id": "gate",
            },
        },
    }

    async def fail_event(*_args, **_kwargs):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(repo, "_insert_event", fail_event)
    with pytest.raises(RuntimeError, match="event write failed"):
        await repo.park_gate(
            "inst",
            "run",
            frontier=[{"node": "gate", "data": {}}],
            visited=["trigger"],
            state=state,
            approval_id="ap-flow",
            approval_payload=state["pending_approval"]["approval_payload"],
            expected_version=claimed["state_version"],
            lease_token=claimed["lease_token"],
        )

    run = await repo.get_run("inst", "run")
    assert run is not None and run["status"] == RunStatus.RUNNING.value
    assert run["checkpoint_seq"] == 0
    assert await db.get_approval("inst", "ap-flow") is None
