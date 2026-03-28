"""
FastAPI server — REST API + WebSocket fan-out + React static file serving.
Runs at http://0.0.0.0:7860 by default (configurable via POTTS_API_HOST / POTTS_API_PORT).
"""

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from potts.state import state
from potts.memory.store import (
    load_facts,
    load_recent_sessions,
    load_all_scheduled_tasks,
)

app = FastAPI(title="Potts Dashboard API", docs_url=None, redoc_url=None)

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


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    q = state.add_ws_client()
    logger.debug("WebSocket client connected")

    # Send a full snapshot immediately so the dashboard renders without waiting
    try:
        snapshot = {
            "type": "snapshot",
            "voice_active": state.is_voice_active,
            "metrics": state.metrics,
            "agents": [
                {
                    "id": r.id,
                    "name": r.name,
                    "status": r.status,
                    "task": r.task,
                    "output": r.output,
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
            "message": "Potts API running. Dashboard not built yet.",
            "hint": "Run: cd dashboard && npm run build",
        }


# ── Entry point (called from main.py via asyncio.create_task) ─────────────────

async def run_api_server(host: str = "0.0.0.0", port: int = 7860):
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info(f"Dashboard available at http://{host}:{port}")
    try:
        await server.serve()
    except asyncio.CancelledError:
        logger.info("API server stopped.")
        raise
