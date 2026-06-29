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
            "daily_token_budget": config.llm_daily_token_budget,
            "output_tokens_today": output_tokens_today,
        },
        "cache_enabled": config.llm_cache_enabled,
        "recent": recent,
    }


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
