"""
FastAPI server — REST API + WebSocket fan-out + React static file serving.
Runs at http://127.0.0.1:7766 by default (configurable via LHASSA_API_HOST / LHASSA_API_PORT).
"""

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from lhassa.state import state
from lhassa.config import Config
from lhassa.core import db
from lhassa.core.episode import Episode
from lhassa.core.task import Task
from lhassa.security import signing
from lhassa.security.trust import Origin, TrustLevel
from lhassa.memory.store import (
    load_facts,
    load_recent_sessions,
    load_all_scheduled_tasks,
    replace_memory_snapshot,
)

app = FastAPI(title="Lhassa Dashboard API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DASHBOARD_DIST = Path(__file__).parent.parent.parent / "dashboard" / "dist"

_START_TIME = time.time()


# ── REST ───────────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    state.load_audit_events()
    return {
        "voice_active": state.is_voice_active,
        "metrics": state.metrics,
        "agent_count": len(state.agent_runs),
        "uptime_seconds": int(time.time() - _START_TIME),
    }


@app.get("/api/memory/facts")
async def get_facts():
    return await load_facts()


@app.get("/api/memory/sessions")
async def get_sessions():
    return await load_recent_sessions(20)


@app.get("/api/agents")
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


@app.get("/api/scheduled")
async def get_scheduled():
    return await load_all_scheduled_tasks()


@app.get("/api/metrics")
async def get_metrics():
    return state.metrics


@app.get("/api/llm/ledger")
async def get_llm_ledger():
    from lhassa.llm.gateway import gateway

    config = Config()
    rows = await gateway.ledger_rows()
    recent = list(reversed(rows[-20:]))  # newest first
    output_tokens_today = await gateway.daily_output_tokens()

    totals = {
        "llm_calls": state.metrics.get("llm_calls", 0),
        "llm_input_tokens": state.metrics.get("llm_input_tokens", 0),
        "llm_output_tokens": state.metrics.get("llm_output_tokens", 0),
        "llm_cost_usd": state.metrics.get("llm_cost_usd", 0.0),
        "llm_cache_hits": state.metrics.get("llm_cache_hits", 0),
    }

    return {
        "totals": totals,
        "budget": {
            "daily_token_budget": gateway.effective_daily_budget(config),
            "output_tokens_today": output_tokens_today,
        },
        "kill_switch": gateway.kill_switch,
        "cache_enabled": config.llm_cache_enabled,
        "recent": recent,
    }


@app.get("/api/llm/limits")
async def get_llm_limits():
    """Current cost limits: the effective daily token budget, the kill-switch
    state, and how many output tokens have been spent today (UTC)."""
    from lhassa.llm.gateway import gateway

    config = Config()
    return {
        "daily_token_budget": gateway.effective_daily_budget(config),
        "kill_switch": gateway.kill_switch,
        "output_tokens_today": await gateway.daily_output_tokens(),
    }


@app.post("/api/llm/limits")
async def set_llm_limits(payload: dict):
    """Update the runtime daily token budget and/or the kill-switch.

    Body: ``{daily_token_budget?: int >= 0, kill_switch?: bool}``. The budget is
    persisted so it survives a restart; the kill-switch is in-session. Returns the
    same shape as ``GET /api/llm/limits``.
    """
    from lhassa.llm.gateway import gateway

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="limits payload must be a JSON object.")

    if "daily_token_budget" in payload:
        budget = payload["daily_token_budget"]
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise HTTPException(
                status_code=400,
                detail="daily_token_budget must be a non-negative integer (0 = unlimited).",
            )
        gateway.set_daily_token_budget(budget)

    if "kill_switch" in payload:
        kill_switch = payload["kill_switch"]
        if not isinstance(kill_switch, bool):
            raise HTTPException(status_code=400, detail="kill_switch must be a boolean.")
        gateway.set_kill_switch(kill_switch)

    config = Config()
    return {
        "daily_token_budget": gateway.effective_daily_budget(config),
        "kill_switch": gateway.kill_switch,
        "output_tokens_today": await gateway.daily_output_tokens(),
    }


@app.get("/api/llm/ccusage")
async def get_llm_ccusage():
    """Cross-harness accounting from the ``ccusage`` CLI (Claude Code / Codex /
    opencode subscription usage). Defensive: returns ``available: False`` when the
    tool is absent or errors — never blocks the dashboard."""
    from lhassa.llm.ccusage import read_ccusage

    return await read_ccusage()


@app.get("/api/audit")
async def get_audit():
    state.load_audit_events()
    return [asdict(event) for event in state.audit_events]


@app.get("/api/health")
async def get_health():
    state.load_audit_events()
    config = Config()
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


@app.get("/api/export")
async def export_state():
    state.load_audit_events()
    config = Config()
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
        "facts": await load_facts(),
        "sessions": await load_recent_sessions(100),
        "scheduled_tasks": await load_all_scheduled_tasks(),
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


@app.post("/api/import")
async def import_state(payload: dict, dry_run: bool = Query(default=False)):
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
        await replace_memory_snapshot(
            facts=facts,
            sessions=sessions,
            scheduled_tasks=scheduled_tasks,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Import failed: {e}") from e

    summary["dry_run"] = False
    summary["audit_events"] = len(state.audit_events)
    return summary


# ── Multi-machine: check-in (not push) + report (§2.5) ───────────────────────────
#
# The brain side of the "check-in, not push" loop. A thin local agent
# (lhassa.remote.local_agent) on each machine POSTs /checkin with what it can do
# and its current state; the brain reconciles real-vs-desired, mints + signs the
# Tasks it must run, and hands them back. The machine runs each one ONLY after its
# own allowlist gate (§2.6) accepts it, then POSTs the outcome to /report.

def _origen_brain() -> Origin:
    """Phase-0 origin for a brain-minted Task: INTERNO trust, actor 'brain'."""
    return Origin(trust=TrustLevel.INTERNO, actor_id="brain")


def _task_to_json(task: Task) -> dict:
    """Serialize a Task for the wire, carrying both the human-readable ``origen``
    and the flat origin fields so the machine can rebuild it (``Task.from_db_row``)
    and re-derive ``canonical_bytes`` byte-for-byte to verify ``firma``."""
    return {
        "id": task.id,
        "instance_id": task.instance_id,
        "accion": task.accion,
        "objetivo": task.objetivo,
        "params": task.params,
        "origen": {"trust": int(task.origen.trust), "actor_id": task.origen.actor_id},
        "origen_trust": int(task.origen.trust),
        "origen_actor": task.origen.actor_id,
        "estado": task.estado,
        "firma": task.firma,
        "creado_en": task.creado_en,
    }


@app.post("/checkin")
async def checkin(payload: dict):
    """A machine checks in. Reconcile desired-vs-current, mint + sign the Tasks it
    must run, persist the new ones, record a check-in Episode, and return them.

    Body: ``{machine_id, capabilities: list[str], current_state: dict}``.
    Returns ``{"tasks": [task_as_json, ...]}`` — each task carries its ``firma``
    and an ``origen`` (Phase 0: INTERNO, actor 'brain').
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="check-in payload must be a JSON object.")
    machine_id = (payload.get("machine_id") or "").strip()
    if not machine_id:
        raise HTTPException(status_code=400, detail="machine_id is required.")
    current_state = payload.get("current_state") or {}
    if not isinstance(current_state, dict):
        raise HTTPException(status_code=400, detail="current_state must be a JSON object.")

    instance_id = Config.instance_id
    await db.init_db()

    emitted: list[Task] = []
    new_count = 0

    # Pre-existing pending tasks for this instance (snapshot BEFORE we synthesize,
    # so reconciliation's freshly-persisted tasks aren't double-counted). Phase 0
    # keeps it simple — all pending tasks; a machine-targeting field comes later.
    for row in await db.load_pending_tasks(instance_id):
        task = Task.from_db_row(row)
        if not task.firma:
            task.firma = signing.sign_task(task)
        emitted.append(task)

    # Reconcile: a desired key whose value differs from current_state becomes a
    # signed sync_repo Task (Phase 0: desired_state holds repos; git pull is the
    # reconciliation step, §2.5).
    desired = await db.get_desired_state(instance_id, machine_id)
    for clave, valor in desired.items():
        if str(current_state.get(clave)) != str(valor):
            task = Task(
                id=db._new_id("t"),
                instance_id=instance_id,
                accion="sync_repo",
                objetivo=clave,
                params={
                    "repo_dir": clave,
                    "desired": valor,
                    "current": current_state.get(clave),
                },
                origen=_origen_brain(),
                estado="pendiente",
                creado_en=db._now(),
            )
            task.firma = signing.sign_task(task)
            await db.add_task(**task.to_db_kwargs())
            emitted.append(task)
            new_count += 1

    # Observability: explain what the check-in emitted and why.
    ep = Episode.begin(despertar_por=f"checkin:{machine_id}", instance_id=instance_id)
    ep.finish(
        decision="checkin",
        accion="checkin",
        resultado=(
            f"machine {machine_id} checked in; emitted {len(emitted)} task(s) "
            f"({new_count} new from reconciliation)"
        ),
    )
    await ep.append()

    return {"tasks": [_task_to_json(t) for t in emitted]}


# Outcomes that mean the task succeeded; everything else is a failure (with
# "rechazada" kept distinct: gate-denied, never executed).
_OK_OUTCOMES = {"hecha", "ok", "done", "success", "exitosa"}


@app.post("/report")
async def report(payload: dict):
    """A machine reports a task's outcome. Set its state, append an outcome
    Episode. Body: ``{task_id, outcome, detail}``. Returns ``{"ok": true}``.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="report payload must be a JSON object.")
    task_id = (payload.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required.")
    outcome = (payload.get("outcome") or "").strip()
    detail = payload.get("detail") or ""

    instance_id = Config.instance_id
    await db.init_db()

    if outcome == "rechazada":
        estado = "rechazada"
    elif outcome in _OK_OUTCOMES:
        estado = "hecha"
    else:
        estado = "fallida"
    await db.set_task_state(instance_id, task_id, estado)

    ep = Episode.begin(despertar_por=f"report:{task_id}", instance_id=instance_id)
    ep.finish(
        decision="report",
        accion="report",
        resultado=f"task {task_id} → {outcome or 'sin outcome'}: {detail}",
    )
    await ep.append()

    return {"ok": True}


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    q = state.add_ws_client()
    logger.debug("WebSocket client connected")
    state.load_audit_events()

    # Send a full snapshot immediately so the dashboard renders without waiting
    try:
        snapshot = {
            "type": "snapshot",
            "voice_active": state.is_voice_active,
            "metrics": state.metrics,
            "audit": [asdict(event) for event in state.audit_events],
            "agents": [
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
            ],
        }
        await ws.send_text(json.dumps(snapshot))
    except Exception:
        pass

    try:
        while True:
            event = await q.get()
            await ws.send_text(json.dumps(event))
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        state.remove_ws_client(q)


# ── Static files ───────────────────────────────────────────────────────────────

if DASHBOARD_DIST.exists():
    app.mount("/", StaticFiles(directory=DASHBOARD_DIST, html=True), name="static")
else:
    @app.get("/")
    async def root():
        return {
            "message": "Lhassa API running. Dashboard not built yet.",
            "hint": "Run: cd dashboard && npm run build",
        }


# ── Entry point (called from main.py via asyncio.create_task) ─────────────────

async def run_api_server(host: str = "127.0.0.1", port: int = 7766):
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    display_host = "localhost" if host in ("0.0.0.0", "::") else host
    logger.info(f"Dashboard available at http://{display_host}:{port}")
    try:
        await server.serve()
    except asyncio.CancelledError:
        logger.info("API server stopped.")
        raise
