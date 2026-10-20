# SPDX-License-Identifier: AGPL-3.0-or-later
"""
FastAPI server. REST API + WebSocket fan-out + React static file serving.
Runs at http://127.0.0.1:7766 by default (configurable via LHASSA_API_HOST / LHASSA_API_PORT).

This module is the INTEGRATOR: it constructs the app, installs the local-or-token
auth middleware + CORS, mounts every domain router (``include_router``), serves the
WebSocket ``/ws`` fan-out and the built dashboard, and exposes ``run_api_server``.
The REST surface lives in the per-domain ``*_routes`` modules alongside this one.
"""

import asyncio
import hmac
import json
from dataclasses import asdict

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from lhassa.state import state
from lhassa.config import Config

# Re-exported through this module for the runtime router's whole-state export/import
# seam: ``/api/export`` + ``/api/import`` (in api.runtime_routes) resolve these as
# ``server.load_facts`` / ``server.load_recent_sessions`` / …; the single seam the
# endpoint tests patch. They are imported here (not used directly) on purpose.
from lhassa.memory.store import (  # noqa: F401
    load_facts,
    load_recent_sessions,
    load_all_scheduled_tasks,
    replace_memory_snapshot,
)

from lhassa.api._shared import DASHBOARD_DIST

app = FastAPI(title="Lhassa Dashboard API", docs_url=None, redoc_url=None)


def _cors_origins() -> list[str]:
    configured = getattr(Config, "api_cors_origins", "")
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    port = getattr(Config, "api_port", 7766)
    return [
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]


# Methods/headers actually used by the REST surface (see api/*_routes.py). When an
# explicit ``LHASSA_API_CORS_ORIGINS`` is configured (a production deployment naming
# real origins), CORS is tightened to this closed set instead of "*". The default
# (unconfigured, localhost-only) origins keep the permissive "*" behavior; that
# path is already gated by the loopback/token check above and dev tooling (Vite,
# curl, etc.) benefits from not having to match headers/methods exactly.
_CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_CORS_HEADERS = ["Authorization", "Content-Type", "X-Lhassa-Api-Token"]


def _cors_configured() -> bool:
    return bool(getattr(Config, "api_cors_origins", ""))


def _cors_methods() -> list[str]:
    return _CORS_METHODS if _cors_configured() else ["*"]


def _cors_headers() -> list[str]:
    return _CORS_HEADERS if _cors_configured() else ["*"]


_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_loopback(host: str | None) -> bool:
    return (host or "").split("%", 1)[0] in _LOCAL_HOSTS


def _authorized(token: str, provided: str | None) -> bool:
    if not token or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), token.encode("utf-8"))


def _http_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-lhassa-api-token")


def _ws_token(ws: WebSocket) -> str | None:
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ws.headers.get("x-lhassa-api-token") or ws.query_params.get("token")


def _auth_error_detail() -> str:
    return "LHASSA_API_TOKEN is required for non-local API access."


def _request_allowed(client_host: str | None, provided_token: str | None) -> bool:
    token = getattr(Config, "api_token", "")
    if _authorized(token, provided_token):
        return True
    return not token and _is_loopback(client_host)


@app.middleware("http")
async def require_local_or_token(request: Request, call_next):
    path = request.url.path
    if path.startswith(("/api", "/checkin", "/report")):
        client_host = request.client.host if request.client else None
        provided_token = _http_token(request)
        if not _request_allowed(client_host, provided_token):
            return JSONResponse(status_code=403, content={"detail": _auth_error_detail()})
        # Downstream mutation routes may need an authenticated identity, not just
        # an allow/deny verdict.  Record which boundary established access; never
        # derive that identity from a JSON field supplied by the caller.
        token = getattr(Config, "api_token", "")
        request.state.lhassa_auth_source = (
            "api-token" if _authorized(token, provided_token) else "loopback"
        )
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=_cors_methods(),
    allow_headers=_cors_headers(),
)

# ── Feature routers (each owns a cohesive slice of the REST surface) ──────────
# Kept in their own modules; the integrator only mounts them. No router imports
# ``server`` at load time (that would risk a cycle); shared constants live in
# ``api/_shared.py`` and the few call-time seams import ``server`` lazily.
from lhassa.api import (  # noqa: E402
    activity_routes,
    analytics_routes,
    approval_routes,
    artifacts_routes,
    calendar_routes,
    checkin_routes,
    code_routes,
    config_routes,
    fleet_routes,
    flow_run_routes,
    flow_routes,
    integrity_routes,
    llm_routes,
    memory_routes,
    org_routes,
    overview_routes,
    project_routes,
    relationship_routes,
    reports_routes,
    runtime_routes,
    share_routes,
    skills_routes,
    task_routes,
    tools_routes,
)

# Re-exported for the CLI's ``lhassa status``, which reuses the aggregate in-process
# (``lhassa.cli.extra_cmds`` imports ``server.build_overview``: no HTTP round-trip).
from lhassa.api.overview_routes import build_overview  # noqa: F401,E402
from lhassa.api.postulaciones_routes import router as postulaciones_routes

app.include_router(task_routes.router)
app.include_router(analytics_routes.router)
app.include_router(code_routes.router)
app.include_router(relationship_routes.router)
app.include_router(flow_routes.router)
app.include_router(flow_run_routes.router)
app.include_router(integrity_routes.router)
app.include_router(runtime_routes.router)
app.include_router(llm_routes.router)
app.include_router(approval_routes.router)
app.include_router(checkin_routes.router)
app.include_router(memory_routes.router)
app.include_router(tools_routes.router)
app.include_router(config_routes.router)
app.include_router(org_routes.router)
app.include_router(skills_routes.router)
app.include_router(activity_routes.router)
app.include_router(reports_routes.router)
app.include_router(fleet_routes.router)
app.include_router(overview_routes.router)
app.include_router(artifacts_routes.router)
app.include_router(calendar_routes.router)
app.include_router(project_routes.router)
app.include_router(share_routes.router)
app.include_router(postulaciones_routes)

# Capability packs mount their own routers (``extensions.register_api_router``).
# Plugin discovery rides on register_all(); a failing factory is skipped so one
# broken pack never takes the API down.
from lhassa import actionpack as _actionpack  # noqa: E402
from lhassa.extensions import API_ROUTERS  # noqa: E402

_actionpack.register_all()
for _pack_router_name, _pack_router_factory in API_ROUTERS.items().items():
    try:
        app.include_router(_pack_router_factory())
    except Exception as _exc:  # noqa: BLE001
        logger.warning(f"api: pack router {_pack_router_name!r} failed to mount: {_exc}")


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    client_host = ws.client.host if ws.client else None
    if not _request_allowed(client_host, _ws_token(ws)):
        await ws.close(code=1008, reason=_auth_error_detail())
        return
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
