# SPDX-License-Identifier: AGPL-3.0-or-later
"""The agentic run door: own one local-CLI run's lifecycle and stream it.

Shared by every surface that launches an agentic coding task through a local AI
CLI (:mod:`lhassa.agents.local_cli`): the dashboard's Run section
(``POST /api/code`` in the api pack) and the chat ``/code`` approval
(:mod:`lhassa.chat.callbacks`). A run launched from either is
indistinguishable from the other.

``LocalCliAgent.run`` mints its own id internally and only returns the final
output string, so it cannot hand a caller an id up front. To return an id that
*matches* the WS stream, :func:`stream_run` owns the lifecycle itself: the caller
picks the run_id and registers the ``AgentRun``; this module broadcasts
``agent_started`` through :data:`lhassa.state.state`, delegates the subprocess +
per-line ``agent_token`` broadcasts to the agent's ``_invoke`` (bound to that exact
run_id), then emits ``agent_done`` (or ``agent_error``) and persists the run for
analytics. Never raises except on cancellation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

import lhassa.config as lhassa_config
from lhassa.agents import local_cli
from lhassa.core import db
from lhassa.core import projects as projects_mod
from lhassa.harness import context_pack
from lhassa.state import AgentRun, state

__all__ = [
    "agent_cwd",
    "persist_agent_run",
    "resolve_context_pack",
    "stream_run",
]


def agent_cwd() -> Path:
    """Resolve the working directory the same way ``LocalCliAgent.run`` does.

    ``config.agent_dir`` when set, else the process cwd. Never raises.
    """
    try:
        agent_dir = getattr(lhassa_config.Config(), "agent_dir", "")
        return Path(agent_dir).expanduser() if agent_dir else Path.cwd()
    except Exception:
        return Path.cwd()


async def resolve_context_pack(cwd: Path):
    """Resolve a project from the run's cwd and ensure its context pack.

    Returns the pack dir, or ``None`` when no project matches; a launch never
    blocks on this: context is a gift, not a gate.
    """
    try:
        project = await projects_mod.resolve_project(lhassa_config.Config.instance_id, str(cwd))
        if project is None:
            return None
        return await context_pack.ensure(project["id"])
    except Exception as exc:
        logger.debug(f"[code] context pack skipped for cwd={cwd!r}: {exc}")
        return None


async def stream_run(agent, run_id: str, run: AgentRun, task: str) -> None:
    """Own the run lifecycle so the caller's run_id matches the WS stream.

    Mirrors ``LocalCliAgent.run`` (started, tokens, done/error) but with a
    pre-chosen run_id, delegating the subprocess + per-line ``agent_token``
    broadcasts to the agent's ``_invoke``. Never raises; a failure becomes an
    ``agent_error`` event and a finished run, exactly like the CLI path.
    """
    await state.broadcast({
        "type": "agent_started",
        "run_id": run_id,
        "agent": run.name,
        "task": task,
    })
    try:
        cwd = agent_cwd()
        result = await agent._invoke(run_id, run, task, cwd)
        state.finish_agent(run_id, result, success=True)
        await persist_agent_run(run)
        await state.broadcast({"type": "agent_done", "run_id": run_id, "output": result})
    except asyncio.CancelledError:
        state.finish_agent(run_id, run.output or "(cancelled)", success=False)
        raise
    except Exception as e:
        logger.error(f"[code/{getattr(agent, 'cli', '?')}] {e}")
        state.finish_agent(run_id, str(e), success=False)
        await persist_agent_run(run)
        await state.broadcast({"type": "agent_error", "run_id": run_id, "error": str(e)})


def _iso_from_ts(value: float | None) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()


async def persist_agent_run(run: AgentRun) -> None:
    """Persist a completed/stopped CLI run for durable analytics, best-effort."""
    try:
        await db.init_db()
        started = _iso_from_ts(run.started_at)
        finished = _iso_from_ts(run.finished_at)
        duration_ms = 0
        if run.started_at and run.finished_at and run.finished_at >= run.started_at:
            duration_ms = int((run.finished_at - run.started_at) * 1000)
        await db.record_run({
            "id": run.id,
            "instance_id": lhassa_config.Config.instance_id,
            "kind": "cli",
            "entity_id": run.cli or local_cli.detect_ai_cli() or run.name,
            "status": run.status,
            "trigger": "manual",
            "summary": (run.task or "")[:240],
            "started_at": started,
            "finished_at": finished,
            "duration_ms": duration_ms,
            "raw": {
                "id": run.id,
                "name": run.name,
                "cli": run.cli,
                "status": run.status,
                "task": run.task,
                "output": (run.output or "")[-2000:],
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            },
        })
    except Exception as exc:
        logger.debug("[code] analytics persistence skipped for run_id={!r}: {}", run.id, exc)
