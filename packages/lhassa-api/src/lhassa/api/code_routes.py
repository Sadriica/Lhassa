# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Code routes; launch an agentic coding task through a local AI CLI and stream it.

This is the backend behind a "run claude on a task and watch it work" dashboard
panel. The heavy lifting lives in ``lhassa.agents.local_cli.LocalCliAgent``, which
already streams each stdout line to every connected WebSocket via
``state.broadcast({"type": "agent_token", "run_id": …, "token": …})`` plus
``agent_started`` / ``agent_done`` / ``agent_error`` lifecycle events. So the
dashboard's existing ``/ws`` fan-out already carries the stream; these routes only
need to LAUNCH a run and return its id so the panel can correlate the WS events.

Endpoints (mounted by the integrator with ``app.include_router(code_routes.router)``):

  GET  /api/code/clis  → {"clis": [installed CLI names], "default": <auto-detected>}
  POST /api/code       → body {task: str, cli?: str}; launches the run in the
                         background and returns {"ok": true, "run_id", "cli"} at once.
  GET  /api/code/runs  → recent agent runs (same shape as /api/agents) to list/resume.
  POST /api/code/{run_id}/stop → terminate a running task's process group, mark it
                         ``stopped``, and emit ``agent_stopped``/``agent_done``. Idempotent.

run_id handling: this module generates the run_id and registers the ``AgentRun``;
the lifecycle itself (``agent_started``, the agent's ``agent_token`` stream bound to
that exact run_id, ``agent_done``/``agent_error``, analytics persistence) lives in
core, :mod:`lhassa.agents.code_run`, which the Telegram ``/code`` approval launches
through too. The frontend therefore consumes ``agent_token`` keyed by the returned
``run_id``.
"""

import asyncio
import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from lhassa.agents import code_run, local_cli
from lhassa.state import AgentRun, state

router = APIRouter()


class CodeRequest(BaseModel):
    task: str | None = None
    cli: str | None = None


def _error(status: int, message: str) -> JSONResponse:
    """A clear 4xx body; launches never surface as a 500."""
    return JSONResponse(status_code=status, content={"ok": False, "error": message})


@router.get("/api/code/clis")
async def list_clis() -> dict:
    """Installed AI CLIs (for a selector) plus the auto-detected default."""
    return {"clis": local_cli.available_clis(), "default": local_cli.detect_ai_cli()}


@router.get("/api/code/runs")
async def list_runs() -> list[dict]:
    """Recent agent runs (same shape as /api/agents) so the panel can list/resume."""
    return [
        {
            "id": r.id,
            "name": r.name,
            "cli": r.cli,
            "status": r.status,
            "task": r.task,
            "output": r.output,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        }
        for r in state.agent_runs.values()
    ]


@router.post("/api/code/{run_id}/stop")
async def stop_code(run_id: str) -> dict:
    """Stop a running agentic task.

    Terminates the run's subprocess process group (graceful → kill, so child CLIs
    die too), marks the run ``stopped``, and emits ``agent_stopped`` + ``agent_done``
    so the panel updates. Idempotent: stopping an unknown or already-finished run is a
    safe no-op. Never raises; a stop must never take down the server.
    """
    try:
        run = state.agent_runs.get(run_id)
        if run is None or run.status != "running":
            return {"ok": True, "run_id": run_id, "stopped": False}
        # Flip status first so a late finish from the torn-down subprocess can't clobber it,
        # then kill the process group and free the tracking entry.
        state.stop_agent(run_id)
        await local_cli.stop_run(run_id)
        await state.broadcast({"type": "agent_stopped", "run_id": run_id})
        await state.broadcast({"type": "agent_done", "run_id": run_id, "output": run.output})
        await code_run.persist_agent_run(run)
        return {"ok": True, "run_id": run_id, "stopped": True}
    except Exception as e:  # a stop must never surface as a 500
        logger.error(f"[code] stop failed for run_id={run_id!r}: {e}")
        return {"ok": True, "run_id": run_id, "stopped": False}


@router.post("/api/code")
async def launch_code(req: CodeRequest):
    """Launch an agentic coding task; return immediately while it streams over /ws."""
    task = (req.task or "").strip()
    if not task:
        return _error(400, "task must be a non-empty string")

    cli = (req.cli or local_cli.detect_ai_cli()).strip().lower()
    # is_installed is False for both unknown and not-on-PATH CLIs; one clear 400.
    if not local_cli.is_installed(cli):
        installed = ", ".join(local_cli.available_clis()) or "none installed"
        return _error(
            400,
            f"The {cli!r} CLI is not available. Installed CLIs: {installed}. "
            f"Install it or pick one of the installed CLIs.",
        )

    # Defensive: a launch failure is a clear 4xx, never a 500.
    try:
        pack_dir = await code_run.resolve_context_pack(code_run.agent_cwd())
        try:
            agent = local_cli.make_agent(cli, context_pack_dir=pack_dir)
        except TypeError:
            # A caller-supplied make_agent (e.g. a test double) may not accept
            # context_pack_dir; context is a gift, not a gate, so fall back.
            agent = local_cli.make_agent(cli)
        run_id = uuid.uuid4().hex[:8]
        run = AgentRun(id=run_id, name=f"ai-cli ({cli})", status="running", task=task, cli=cli)
        state.register_agent(run)
        asyncio.ensure_future(code_run.stream_run(agent, run_id, run, task))
    except Exception as e:  # never 500; surface a clear, actionable error
        logger.error(f"[code] launch failed for cli={cli!r}: {e}")
        return _error(400, f"Could not launch {cli!r}: {e}")

    return {"ok": True, "run_id": run_id, "cli": cli}
