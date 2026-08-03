# SPDX-License-Identifier: AGPL-3.0-or-later
"""Activity routes — agents, scheduled tasks, and the audit log (read-only).

The dashboard's "what has the brain been doing" reads:

  * ``/api/agents`` — the in-memory agent-run state.
  * ``/api/scheduled`` — ``send_reminder`` tasks from the ``tasks`` table
    (``core/db.py``), reshaped into the legacy ``scheduled_tasks`` response
    keys the dashboard's ``ScheduledPanel``/``WatchlistPanel`` already expect
    (``id``/``name``/``run_at``/``message``/``status``/``created_at``). See
    ``doc/scheduler.md``.
  * ``/api/audit`` — the audit-log read.

All read-only. Mounted by the integrator with
``app.include_router(activity_routes.router)``.
"""

from dataclasses import asdict

from fastapi import APIRouter

from lhassa.config import Config
from lhassa.core import db as _db
from lhassa.state import state

router = APIRouter()

# Legacy → tasks-table estado mapping for the dashboard's read (it only
# distinguishes "pending" from everything else).
_ESTADO_TO_STATUS = {
    "pendiente": "pending",
    "esperando_aprobacion": "pending",
    "hecha": "done",
    "rechazada": "cancelled",
    "diferida": "pending",
}


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
    await _db.init_db()
    instance_id = Config.instance_id
    tasks = await _db.list_tasks(instance_id, limit=50)
    reminders = [t for t in tasks if t.get("accion") == "send_reminder"]
    return [
        {
            "id": t.get("id"),
            "name": t.get("objetivo") or "Reminder",
            "run_at": t.get("due_at") or t.get("creado_en"),
            "message": (t.get("params") or {}).get("text", ""),
            "status": _ESTADO_TO_STATUS.get(t.get("estado"), t.get("estado")),
            "created_at": t.get("creado_en"),
        }
        for t in reminders
    ]


@router.get("/api/audit")
async def get_audit():
    state.load_audit_events()
    return [asdict(event) for event in state.audit_events]
