"""Operational read API for durable flow executions."""

from fastapi import APIRouter, HTTPException, Query

import lhassa.config as lhassa_config
from lhassa.flows.durable import record_for_run
from lhassa.flows.runtime import FlowRuntimeRepository, RunStatus

router = APIRouter(prefix="/api/flow-runs", tags=["flow-runs"])


@router.get("")
async def list_flow_runs(
    status: RunStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    """List operational runs for the configured instance."""
    repo = FlowRuntimeRepository()
    rows = await repo.list_runs(
        lhassa_config.Config.instance_id,
        statuses=[status] if status is not None else None,
        limit=limit,
    )
    return [
        {
            "id": row["run_id"],
            "flow_id": row["flow_id"],
            "status": row["status"],
            "checkpoint_seq": row["checkpoint_seq"],
            "state_version": row["state_version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


@router.get("/{run_id}")
async def get_flow_run(run_id: str) -> dict:
    """Return the public record reconstructed from operational state."""
    repo = FlowRuntimeRepository()
    try:
        return await record_for_run(repo, lhassa_config.Config.instance_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"flow run {run_id!r} not found") from exc


@router.get("/{run_id}/events")
async def get_flow_run_events(run_id: str) -> list[dict]:
    """Return the append-only event stream for one instance-scoped run."""
    repo = FlowRuntimeRepository()
    if await repo.get_run(lhassa_config.Config.instance_id, run_id) is None:
        raise HTTPException(status_code=404, detail=f"flow run {run_id!r} not found")
    return await repo.list_events(lhassa_config.Config.instance_id, run_id)
