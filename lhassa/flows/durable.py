"""Durable orchestration around the graph engine.

The engine remains useful as an offline, injected-executor component.  This
module binds it to :class:`FlowRuntimeRepository`: snapshots are frozen before
execution, every cursor becomes a checkpoint, and terminal/waiting states are
committed with the worker's fencing token.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Optional

from lhassa.flows.engine import Executor, run_flow
from lhassa.flows.model import Flow
from lhassa.flows.runtime import (
    FlowEventKind,
    FlowRuntimeRepository,
    RunStatus,
    RuntimeConflict,
)

if TYPE_CHECKING:
    from lhassa.config import Config


def _digest(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _public_record(record: dict) -> dict:
    out = dict(record)
    out.pop("resume_state", None)
    return out


async def _execute_claimed(
    flow: Flow,
    run: dict,
    *,
    repo: FlowRuntimeRepository,
    config: "Config",
    inputs: Optional[dict] = None,
    company: Optional[str] = None,
    base_cv: Optional[str] = None,
    resume_state: Optional[dict] = None,
    executor: Optional[Executor] = None,
) -> dict:
    """Execute one fenced claim and persist every engine cursor."""
    lease_token = run.get("lease_token")
    if not lease_token:
        raise RuntimeConflict("durable execution requires a claimed lease")
    state_version = int(run["state_version"])
    parked = False

    async def save_cursor(cursor: dict) -> None:
        nonlocal parked, state_version
        pending = cursor.get("pending_approval") or {}
        if cursor.get("status") == "waiting_approval":
            await repo.park_gate(
                run["instance_id"],
                run["run_id"],
                frontier=list(cursor.get("frontier") or []),
                visited=list(cursor.get("visited") or []),
                state=cursor,
                approval_id=str(pending.get("approval_id") or ""),
                approval_payload=dict(pending.get("approval_payload") or {}),
                expected_version=state_version,
                lease_token=lease_token,
            )
            parked = True
        else:
            await repo.append_checkpoint(
                run["instance_id"],
                run["run_id"],
                frontier=list(cursor.get("frontier") or []),
                visited=list(cursor.get("visited") or []),
                state=cursor,
                expected_version=state_version,
                lease_token=lease_token,
            )
        state_version += 1

    async def renew_lease() -> None:
        while True:
            await asyncio.sleep(20)
            try:
                await repo.heartbeat_run(
                    run["instance_id"],
                    run["run_id"],
                    lease_token=lease_token,
                    lease_seconds=60,
                )
            except RuntimeConflict:
                current = await repo.get_run(run["instance_id"], run["run_id"])
                if current is not None and current["status"] != RunStatus.RUNNING.value:
                    return
                raise

    heartbeat = asyncio.create_task(renew_lease())
    try:
        record = await run_flow(
            flow,
            inputs=inputs,
            config=config,
            executor=executor,
            company=company,
            base_cv=base_cv,
            run_id=run["run_id"],
            resume_state=resume_state,
            checkpoint_hook=save_cursor,
            approval_context={
                "instance_id": run["instance_id"],
                "owner_department": run.get("owner_department") or "",
            },
        )
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
    record["duration_ms"] = sum(
        int(step.get("duration_ms") or 0) for step in record.get("steps") or []
    )
    status = record.get("status")
    if status == "waiting_approval":
        if not parked:
            raise RuntimeConflict("approval run returned without an atomic park")
    else:
        failed = status == "failed"
        await repo.transition_run(
            run["instance_id"],
            run["run_id"],
            expected_status=RunStatus.RUNNING,
            new_status=RunStatus.FAILED if failed else RunStatus.COMPLETED,
            event_kind=(
                FlowEventKind.RUN_FAILED if failed else FlowEventKind.RUN_COMPLETED
            ),
            expected_version=state_version,
            lease_token=lease_token,
            output=record.get("output"),
            error=(
                next(
                    (
                        str(step.get("error"))
                        for step in reversed(record.get("steps") or [])
                        if step.get("error")
                    ),
                    "flow failed",
                )
                if failed
                else None
            ),
        )
    return _public_record(record)


async def start_flow_run(
    flow: Flow,
    *,
    inputs: Optional[dict] = None,
    config: Optional["Config"] = None,
    company: Optional[str] = None,
    base_cv: Optional[str] = None,
    executor: Optional[Executor] = None,
    idempotency_key: Optional[str] = None,
    owner_department: str = "",
    org_chain_snapshot: Optional[list] = None,
    repo: Optional[FlowRuntimeRepository] = None,
) -> dict:
    """Create, claim, and execute a durable flow run in the caller's process."""
    from lhassa.config import Config as CurrentConfig

    config = config or CurrentConfig()
    repo = repo or FlowRuntimeRepository()
    instance_id = getattr(config, "instance_id", None) or CurrentConfig.instance_id
    snapshot = flow.to_dict()
    created = await repo.create_run(
        instance_id,
        flow.id,
        snapshot,
        inputs=inputs or {},
        flow_digest=_digest(snapshot),
        requested_by="api",
        owner_department=owner_department,
        org_chain_snapshot=org_chain_snapshot or [],
        topology_digest=_digest({"chain": org_chain_snapshot or []}),
        idempotency_key=idempotency_key,
    )
    if created["status"] != RunStatus.QUEUED.value:
        return await record_for_run(repo, instance_id, created["run_id"])
    if await repo.get_checkpoint(instance_id, created["run_id"]) is not None:
        return await resume_flow_run(
            instance_id,
            created["run_id"],
            config=config,
            executor=executor,
            repo=repo,
        )
    claimed = await repo.claim_run(
        instance_id,
        f"inline:{uuid.uuid4().hex[:12]}",
        run_id=created["run_id"],
    )
    if claimed is None:
        return await record_for_run(repo, instance_id, created["run_id"])
    stored_flow = Flow.from_dict(claimed["flow_snapshot"])
    return await _execute_claimed(
        stored_flow,
        claimed,
        repo=repo,
        config=config,
        inputs=claimed["inputs"],
        company=company,
        base_cv=base_cv,
        executor=executor,
    )


async def resume_flow_run(
    instance_id: str,
    run_id: str,
    *,
    config: Optional["Config"] = None,
    executor: Optional[Executor] = None,
    repo: Optional[FlowRuntimeRepository] = None,
) -> dict:
    """Claim a queued run and resume it from its latest durable checkpoint."""
    from lhassa.config import Config as CurrentConfig

    config = config or CurrentConfig()
    setattr(config, "instance_id", instance_id)
    repo = repo or FlowRuntimeRepository()
    run = await repo.get_run(instance_id, run_id)
    if run is None:
        raise KeyError(run_id)
    if run["status"] != RunStatus.QUEUED.value:
        raise RuntimeConflict(f"run {run_id} is not queued")
    checkpoint = await repo.get_checkpoint(instance_id, run_id)
    if checkpoint is None:
        raise RuntimeConflict(f"run {run_id} has no checkpoint")
    claimed = await repo.claim_run(
        instance_id,
        f"inline:{uuid.uuid4().hex[:12]}",
        run_id=run_id,
    )
    if claimed is None:
        return await record_for_run(repo, instance_id, run_id)
    flow = Flow.from_dict(claimed["flow_snapshot"])
    return await _execute_claimed(
        flow,
        claimed,
        repo=repo,
        config=config,
        resume_state=checkpoint["state"],
        executor=executor,
    )


async def resolve_flow_gate(
    instance_id: str,
    approval_id: str,
    *,
    approved: bool,
    by_actor: str,
    config: Optional["Config"] = None,
    executor: Optional[Executor] = None,
    repo: Optional[FlowRuntimeRepository] = None,
) -> dict:
    """Atomically resolve a gate, then resume approved work inline."""
    repo = repo or FlowRuntimeRepository()
    result = await repo.resolve_gate_approval(
        instance_id,
        approval_id,
        approved=approved,
        actor_id=by_actor,
    )
    run = result["run"]
    record = (
        await resume_flow_run(
            instance_id,
            run["run_id"],
            config=config,
            executor=executor,
            repo=repo,
        )
        if approved
        else await record_for_run(repo, instance_id, run["run_id"])
    )
    return {
        "approval_id": approval_id,
        "approved": approved,
        "run_id": run["run_id"],
        "run_status": record["status"],
        "run": record,
        "trigger": (
            str(run["inputs"].get("trigger"))
            if isinstance(run.get("inputs"), dict) and run["inputs"].get("trigger")
            else "manual"
        ),
    }


async def record_for_run(
    repo: FlowRuntimeRepository, instance_id: str, run_id: str
) -> dict:
    """Build a public run record from operational state and its latest cursor."""
    run = await repo.get_run(instance_id, run_id)
    if run is None:
        raise KeyError(run_id)
    checkpoint = await repo.get_checkpoint(instance_id, run_id)
    state = checkpoint["state"] if checkpoint else {}
    return {
        "id": run_id,
        "flow_id": run["flow_id"],
        "status": run["status"],
        "started_at": run["created_at"],
        "finished_at": (
            run["updated_at"]
            if run["status"]
            in {status.value for status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)}
            else None
        ),
        "duration_ms": 0,
        "cost_usd": round(
            sum(float(step.get("cost_usd") or 0.0) for step in state.get("steps") or []),
            6,
        ),
        "steps": state.get("steps") or [],
        "output": run.get("output") if run.get("output") is not None else state.get("last_output"),
    }


__all__ = [
    "record_for_run",
    "resolve_flow_gate",
    "resume_flow_run",
    "start_flow_run",
]
