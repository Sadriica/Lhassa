"""
Code routes — launch an agentic coding task through a local AI CLI and stream it.

This is the backend behind a "run claude on a task and watch it work" dashboard
panel. The heavy lifting lives in ``lhassa.agents.local_cli.LocalCliAgent``, which
already streams each stdout line to every connected WebSocket via
``state.broadcast({"type": "agent_token", "run_id": …, "token": …})`` plus
``agent_started`` / ``agent_done`` / ``agent_error`` lifecycle events. So the
dashboard's existing ``/ws`` fan-out already carries the stream — these routes only
need to LAUNCH a run and return its id so the panel can correlate the WS events.

Endpoints (mounted by the integrator with ``app.include_router(code_routes.router)``):

  GET  /api/code/clis  → {"clis": [installed CLI names], "default": <auto-detected>}
  POST /api/code       → body {task: str, cli?: str}; launches the run in the
                         background and returns {"ok": true, "run_id", "cli"} at once.
  GET  /api/code/runs  → recent agent runs (same shape as /api/agents) to list/resume.
  POST /api/code/{run_id}/stop → terminate a running task's process group, mark it
                         ``stopped``, and emit ``agent_stopped``/``agent_done``. Idempotent.

run_id handling: ``LocalCliAgent.run`` mints its own id internally and only returns
the final output string, so it cannot hand us an id up front. To return an id that
*matches* the WS stream, this module owns the run lifecycle itself — it generates the
run_id, registers the ``AgentRun``, broadcasts ``agent_started``, and delegates the
actual subprocess streaming to the agent's ``_invoke`` (which emits ``agent_token``
events bound to that exact run_id). The frontend therefore consumes ``agent_token``
keyed by the returned ``run_id``.
"""

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from lhassa.agents import local_cli
from lhassa.state import AgentRun, state

router = APIRouter()


class CodeRequest(BaseModel):
    task: str | None = None
    cli: str | None = None


def _error(status: int, message: str) -> JSONResponse:
    """A clear 4xx body — launches never surface as a 500."""
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
    safe no-op. Never raises — a stop must never take down the server.
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
    # is_installed is False for both unknown and not-on-PATH CLIs — one clear 400.
    if not local_cli.is_installed(cli):
        installed = ", ".join(local_cli.available_clis()) or "none installed"
        return _error(
            400,
            f"The {cli!r} CLI is not available. Installed CLIs: {installed}. "
            f"Install it or pick one of the installed CLIs.",
        )

    # Defensive: a launch failure is a clear 4xx, never a 500.
    try:
        agent = local_cli.make_agent(cli)
        run_id = uuid.uuid4().hex[:8]
        run = AgentRun(id=run_id, name=f"ai-cli ({cli})", status="running", task=task, cli=cli)
        state.register_agent(run)
        asyncio.ensure_future(_stream_run(agent, run_id, run, task))
    except Exception as e:  # never 500 — surface a clear, actionable error
        logger.error(f"[code] launch failed for cli={cli!r}: {e}")
        return _error(400, f"Could not launch {cli!r}: {e}")

    return {"ok": True, "run_id": run_id, "cli": cli}


async def _stream_run(agent, run_id: str, run: AgentRun, task: str) -> None:
    """Own the run lifecycle so the returned run_id matches the WS stream.

    Mirrors ``LocalCliAgent.run`` (started → tokens → done/error) but with our
    pre-chosen run_id, delegating the subprocess + per-line ``agent_token``
    broadcasts to the agent's ``_invoke``. Never raises — a failure becomes an
    ``agent_error`` event and a finished run, exactly like the CLI path.
    """
    await state.broadcast({
        "type": "agent_started",
        "run_id": run_id,
        "agent": run.name,
        "task": task,
    })
    try:
        cwd = _agent_cwd()
        result = await agent._invoke(run_id, run, task, cwd)
        state.finish_agent(run_id, result, success=True)
        await state.broadcast({"type": "agent_done", "run_id": run_id, "output": result})
    except asyncio.CancelledError:
        state.finish_agent(run_id, run.output or "(cancelled)", success=False)
        raise
    except Exception as e:
        logger.error(f"[code/{getattr(agent, 'cli', '?')}] {e}")
        state.finish_agent(run_id, str(e), success=False)
        await state.broadcast({"type": "agent_error", "run_id": run_id, "error": str(e)})


def _agent_cwd() -> Path:
    """Resolve the working directory the same way LocalCliAgent.run does."""
    try:
        from lhassa.config import Config

        agent_dir = getattr(Config(), "agent_dir", "")
        return Path(agent_dir).expanduser() if agent_dir else Path.cwd()
    except Exception:
        return Path.cwd()
