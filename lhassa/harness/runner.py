"""
The PID/stream runner (§2.1, §2.7) — "lanza y duerme".

``launch`` starts a ``HarnessAdapter``'s command as a *separate* subprocess, records
a ``jobs`` row (PID, harness, estado="running"), and fires a background task that
streams ``parse_line`` events to ``state.broadcast`` while accumulating live cost.
It returns the **job_id** immediately and never blocks the caller (the brain keeps
its bookkeeping serial; the work runs in parallel by PID — §2.7).

``supervise`` is the next-tick reaper: it checks running jobs, reaps the finished
ones, writes their final estado/costo/terminado_en back to the ``jobs`` table and
to ``state.agent_runs``, and returns a summary. ``step()`` calls it each tick.

Graceful by construction: a missing CLI records an ``"error: harness unavailable"``
job and never raises; a run that exceeds the timeout is killed and marked error.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import db
from lhassa.harness.adapter import HarnessAdapter
from lhassa.state import AgentRun, state

# Default wall-clock cap for a single delegated run; override with LHASSA_HARNESS_TIMEOUT.
try:
    _DEFAULT_TIMEOUT = int(os.getenv("LHASSA_HARNESS_TIMEOUT", "1800"))
except ValueError:
    _DEFAULT_TIMEOUT = 1800


@dataclass
class _RunningJob:
    """In-memory handle for a launched job (RAM is disposable; the row is truth)."""

    job_id: str
    instance_id: str
    harness: str
    proc: object
    run: AgentRun
    cost_usd: float = 0.0
    estado: str = "running"
    task: Optional[asyncio.Task] = field(default=None)


# Module-level registry of live jobs (apuntadores vivos — §2.3). supervise() reaps
# them; a reaped job is removed (becomes an archived row in the jobs table).
_JOBS: dict[str, _RunningJob] = {}


def running_jobs() -> list[str]:
    """Return the ids of jobs still streaming in this process."""
    return list(_JOBS)


def running_pid(job_id: str) -> Optional[int]:
    """Return the PID of a live job (or ``None`` if unknown/unavailable)."""
    rj = _JOBS.get(job_id)
    proc = getattr(rj, "proc", None) if rj else None
    return getattr(proc, "pid", None) if proc is not None else None


async def launch(
    adapter: HarnessAdapter,
    prompt: str,
    *,
    instance_id: Optional[str] = None,
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    """Launch ``adapter`` on ``prompt`` as a background process. Returns the job_id.

    Never blocks the caller and never raises: a missing CLI is recorded as an error
    job. Streaming + cost accumulation happen in a fire-and-forget asyncio task;
    ``supervise`` reaps the job on a later tick.
    """
    instance_id = instance_id or Config.instance_id
    work_dir = Path(cwd).expanduser() if cwd else _default_cwd()
    timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT

    # Graceful degradation — the muscle is not installed.
    if not adapter.available():
        job_id = await db.record_job(
            instance_id, pid=None, harness=adapter.name,
            estado="error: harness unavailable", costo_usd=0.0,
        )
        logger.warning(f"[harness/{adapter.name}] unavailable — recorded error job {job_id}")
        await state.broadcast({
            "type": "agent_error", "run_id": job_id,
            "error": f"harness {adapter.name} unavailable",
        })
        return job_id

    argv = adapter.build_command(prompt)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as exc:
        job_id = await db.record_job(
            instance_id, pid=None, harness=adapter.name,
            estado="error: harness unavailable", costo_usd=0.0,
        )
        logger.warning(f"[harness/{adapter.name}] spawn failed ({exc}) — error job {job_id}")
        return job_id

    job_id = await db.record_job(
        instance_id, pid=proc.pid, harness=adapter.name,
        estado="running", costo_usd=0.0,
    )
    run = AgentRun(id=job_id, name=f"harness ({adapter.name})", status="running", task=prompt)
    state.register_agent(run)
    rj = _RunningJob(job_id, instance_id, adapter.name, proc, run)
    _JOBS[job_id] = rj
    rj.task = asyncio.create_task(_stream(adapter, rj, timeout))

    logger.info(f"[harness/{adapter.name}] launched job {job_id} pid={proc.pid}")
    await state.broadcast({
        "type": "agent_started", "run_id": job_id,
        "agent": run.name, "task": prompt,
    })
    return job_id


async def _stream(adapter: HarnessAdapter, rj: _RunningJob, timeout: int) -> None:
    """Drain the process, parse + broadcast events, accumulate cost. Never raises."""
    proc = rj.proc

    async def _drain() -> None:
        while True:
            line_bytes = await proc.stdout.readline()  # type: ignore[union-attr]
            if not line_bytes:
                break
            raw = line_bytes.decode(errors="replace")
            ev = adapter.parse_line(raw)
            if ev is None:
                continue
            if ev.cost_usd is not None:
                rj.cost_usd += ev.cost_usd
            if ev.payload:
                rj.run.output += ev.payload + "\n"
            if ev.kind == "error":
                rj.estado = "error"
            await state.broadcast({
                "type": "agent_token", "run_id": rj.job_id,
                "token": ev.payload, "kind": ev.kind,
            })
        await proc.wait()  # type: ignore[union-attr]

    try:
        await asyncio.wait_for(_drain(), timeout=timeout)
        rc = getattr(proc, "returncode", 0)
        if rj.estado != "error":
            rj.estado = "done" if rc in (0, None) else "error"
    except asyncio.TimeoutError:
        _kill(proc)
        rj.estado = "error"
        rj.run.output += f"\n(timed out after {timeout}s)"
        logger.warning(f"[harness/{rj.harness}] job {rj.job_id} timed out — killed")
    except asyncio.CancelledError:
        _kill(proc)
        rj.estado = "error"
        raise
    except Exception as exc:  # never let the background task crash silently
        rj.estado = "error"
        rj.run.output += f"\n{exc}"
        logger.error(f"[harness/{rj.harness}] job {rj.job_id} stream error: {exc}")


def _kill(proc) -> None:
    """Best-effort kill of a process double (real or fake)."""
    try:
        proc.kill()
    except Exception:
        pass


async def supervise(instance_id: Optional[str] = None) -> list[dict]:
    """Reap finished jobs: write final estado/costo/terminado_en; return a summary.

    Cheap to call every tick. Only jobs whose streaming task has completed are
    reaped; still-running jobs are left in place for a later tick (disk supervision,
    never blocking — §2.1). Scoped to ``instance_id`` when given.
    """
    summary: list[dict] = []
    for job_id, rj in list(_JOBS.items()):
        if instance_id is not None and rj.instance_id != instance_id:
            continue
        task = rj.task
        if task is None or not task.done():
            continue

        estado = rj.estado if rj.estado in ("done", "error") else "done"
        await db.update_job(
            rj.instance_id, job_id,
            estado=estado, costo_usd=rj.cost_usd, terminado_en=db._now(),
        )
        state.finish_agent(job_id, rj.run.output.strip(), success=(estado == "done"))
        await state.broadcast({
            "type": "agent_done", "run_id": job_id,
            "output": rj.run.output.strip(), "estado": estado,
        })
        del _JOBS[job_id]
        summary.append({
            "job_id": job_id, "harness": rj.harness,
            "estado": estado, "costo_usd": rj.cost_usd,
        })
        logger.info(
            f"[harness/{rj.harness}] reaped job {job_id} "
            f"estado={estado} costo=${rj.cost_usd:.4f}"
        )
    return summary


def _default_cwd() -> Path:
    """The directory delegated work runs in (LHASSA_AGENT_DIR or the launch cwd)."""
    agent_dir = getattr(Config, "agent_dir", "") or os.getenv("LHASSA_AGENT_DIR", "")
    return Path(agent_dir).expanduser() if agent_dir else Path.cwd()
