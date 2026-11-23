# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runtime routes; status, health, metrics, and whole-state export/import.

The dashboard's runtime surface: the liveness ``/api/status`` + ``/api/health``
snapshots, the raw ``/api/metrics`` blob, and whole-brain state ``/api/export`` /
``/api/import`` (the audit log + the memory snapshot).

Mounted by the integrator with ``app.include_router(runtime_routes.router)``.

Two deliberate seams keep behaviour identical to the pre-split server:
  * ``Config`` is read through the ``lhassa.config`` MODULE (``lhassa_config.Config``)
    rather than a bound name, so a config reload (some endpoint tests reload it) is
    picked up live; exactly as the integrator's own routes see it.
  * ``/api/export`` and ``/api/import`` read the memory-store functions through the
    ``server`` module (a call-time import, never at load; no cycle). Those functions
    are the integrator's single seam; reading them there preserves the existing
    endpoint tests, which patch ``server.load_facts`` / ``server.replace_memory_snapshot``.
"""

import time
from dataclasses import asdict

from lhassa import legal as _legal

from fastapi import APIRouter, HTTPException, Query

import lhassa.config as lhassa_config
from lhassa.state import state

from lhassa.api._shared import DASHBOARD_DIST

router = APIRouter()

_START_TIME = time.time()


@router.get("/api/status")
async def get_status():
    state.load_audit_events()
    return {
        "voice_active": state.is_voice_active,
        "metrics": state.metrics,
        "agent_count": len(state.agent_runs),
        "uptime_seconds": int(time.time() - _START_TIME),
        # Appropriate Legal Notice; required by the AGPL-3.0 section 7(b) term
        # in LICENSE. Rendered from lhassa.legal, asserted by
        # tests/test_legal_notice.py; do not inline or remove it.
        "legal": _legal.notice_dict(),
    }


@router.get("/api/legal")
async def get_legal():
    """The Appropriate Legal Notice, on its own endpoint.

    Section 13 of the AGPL requires a network-deployed instance to offer its
    users the corresponding source. This is that offer, machine-readable and
    always available.
    """
    return _legal.notice_dict()


@router.get("/api/metrics")
async def get_metrics():
    return state.metrics


@router.get("/api/health")
async def get_health():
    state.load_audit_events()
    config = lhassa_config.Config()
    return {
        "voice_active": state.is_voice_active,
        "uptime_seconds": int(time.time() - _START_TIME),
        "ws_clients": len(state._ws_clients),
        "agent_runs": len(state.agent_runs),
        "dashboard_built": DASHBOARD_DIST.exists(),
        "llm_provider": config.llm_provider,
        "model": config.model,
        "agent_backend": config.agent_backend,
        "vision_provider": config.vision_provider,
        "stt_language": config.stt_language,
        "allow_destructive_shell": config.allow_destructive_shell,
        "file_write_root": config.file_write_root or None,
        "api_host": config.api_host,
        "api_port": config.api_port,
    }


@router.get("/api/export")
async def export_state():
    from lhassa.api import server  # call-time seam (no import cycle); see module docstring

    state.load_audit_events()
    config = lhassa_config.Config()
    return {
        "exported_at": int(time.time()),
        "runtime": {
            "voice_active": state.is_voice_active,
            "uptime_seconds": int(time.time() - _START_TIME),
            "llm_provider": config.llm_provider,
            "model": config.model,
            "agent_backend": config.agent_backend,
            "vision_provider": config.vision_provider,
            "stt_language": config.stt_language,
            "allow_destructive_shell": config.allow_destructive_shell,
            "file_write_root": config.file_write_root or None,
        },
        "metrics": state.metrics,
        "audit": [asdict(event) for event in state.audit_events],
        "facts": await server.load_facts(),
        "sessions": await server.load_recent_sessions(100),
        "scheduled_tasks": await server.load_all_scheduled_tasks(),
    }


def _validate_import_payload(payload: dict) -> tuple[list, list, list, list]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Import payload must be a JSON object.")

    audit = payload.get("audit", [])
    facts = payload.get("facts", [])
    sessions = payload.get("sessions", [])
    scheduled_tasks = payload.get("scheduled_tasks", [])

    if not all(isinstance(value, list) for value in (audit, facts, sessions, scheduled_tasks)):
        raise HTTPException(
            status_code=400,
            detail="Import payload fields audit, facts, sessions, and scheduled_tasks must be arrays.",
        )

    return audit, facts, sessions, scheduled_tasks


@router.post("/api/import")
async def import_state(payload: dict, dry_run: bool = Query(default=False)):
    from lhassa.api import server  # call-time seam (no import cycle); see module docstring

    audit, facts, sessions, scheduled_tasks = _validate_import_payload(payload)

    summary = {
        "ok": True,
        "dry_run": dry_run,
        "audit_events": len(audit),
        "facts": len(facts),
        "sessions": len(sessions),
        "scheduled_tasks": len(scheduled_tasks),
    }

    if dry_run:
        return summary

    try:
        state.replace_audit_events(audit)
        await server.replace_memory_snapshot(
            facts=facts,
            sessions=sessions,
            scheduled_tasks=scheduled_tasks,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Import failed: {e}") from e

    summary["dry_run"] = False
    summary["audit_events"] = len(state.audit_events)
    return summary
