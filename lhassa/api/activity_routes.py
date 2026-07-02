"""Activity routes — agents, scheduled tasks, and the audit log (read-only).

The dashboard's "what has the brain been doing" reads:

  * ``/api/agents`` — the in-memory agent-run state.
  * ``/api/scheduled`` — the scheduled-tasks read (from the memory store).
  * ``/api/audit`` — the audit-log read.

All read-only. Mounted by the integrator with
``app.include_router(activity_routes.router)``.
"""

from dataclasses import asdict

from fastapi import APIRouter

from lhassa.memory.store import load_all_scheduled_tasks
from lhassa.state import state

router = APIRouter()


@router.get("/api/agents")
async def get_agents():
    return [
        {
            "id": r.id,
            "name": r.name,
            "status": r.status,
            "task": r.task,
            "output": r.output,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        }
        for r in state.agent_runs.values()
    ]


@router.get("/api/scheduled")
async def get_scheduled():
    return await load_all_scheduled_tasks()


@router.get("/api/audit")
async def get_audit():
    state.load_audit_events()
    return [asdict(event) for event in state.audit_events]
