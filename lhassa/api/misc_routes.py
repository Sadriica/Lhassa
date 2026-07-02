"""Misc read-only routes — agents, audit, scheduled, reports, projections, fleet,
and the dashboard-home ``/api/overview`` aggregate.

The remaining singletons of the dashboard surface, gathered here so the integrator
stays a pure composer:

  * ``/api/agents`` / ``/api/audit`` — in-memory state + audit-log reads.
  * ``/api/scheduled`` — the scheduled-tasks read (from the memory store).
  * Phase-6 read-only surfaces ``/api/reports`` / ``/api/projections`` / ``/api/fleet``
    (episodes summary, cost projection off the gateway ledger, and the multi-machine
    fleet). All read-only and instance-scoped.
  * ``/api/overview`` — one defensive aggregate for the dashboard home / ``lhassa
    status`` (``build_overview``, reused in-process by the CLI, no HTTP).

``Config`` is read through the ``lhassa.config`` MODULE (``lhassa_config.Config``)
rather than a bound name, so a config reload (some endpoint tests reload it) is
picked up live — the same seam ``runtime_routes`` uses.

Mounted by the integrator with ``app.include_router(misc_routes.router)``.
"""

import time
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Query

import lhassa.config as lhassa_config
from lhassa.core import db
from lhassa.memory.store import load_all_scheduled_tasks
from lhassa.security import approval
from lhassa.state import state

router = APIRouter()


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
    return await load_all_scheduled_tasks()


@router.get("/api/audit")
async def get_audit():
    state.load_audit_events()
    return [asdict(event) for event in state.audit_events]


# ── Phase 6: reports / projections / fleet (read-only, additive) ─────────────────
#
# Rich dashboard surfaces for "madurez": what the brain has been doing (reports),
# where cost is trending (projections, off the same ledger/ccusage as
# /api/llm/*), and the multi-machine fleet (last check-in + desired_state). All
# read-only and instance-scoped, matching the existing endpoints' await style.

@router.get("/api/reports")
async def get_reports(n: int = Query(default=50, ge=1, le=500)):
    """Recent episodes + an outcomes summary (counts by wake trigger / decision)."""
    await db.init_db()
    rows = await db.load_recent_episodes(lhassa_config.Config.instance_id, n)

    by_despertar: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    for r in rows:
        trigger = str(r.get("despertar_por") or "?").split(":", 1)[0]
        by_despertar[trigger] = by_despertar.get(trigger, 0) + 1
        decision = str(r.get("decision") or "?")
        by_decision[decision] = by_decision.get(decision, 0) + 1

    recent = [
        {
            "id": r.get("id"),
            "creado_en": r.get("creado_en"),
            "despertar_por": r.get("despertar_por"),
            "decision": r.get("decision"),
            "accion": r.get("accion"),
            "resultado": r.get("resultado"),
            "dominio": r.get("dominio"),
            "costo_usd": r.get("costo_usd"),
        }
        for r in rows
    ]
    return {
        "count": len(rows),
        "by_despertar": by_despertar,
        "by_decision": by_decision,
        "recent": recent,
    }


@router.get("/api/projections")
async def get_projections():
    """Cost projection from the gateway ledger (same data as /api/llm/*).

    Spend so far today + a simple linear projection to day/month end. Pricing is
    NOT duplicated — the ledger already carries per-call ``cost_usd``. The
    cross-harness ccusage read is included (defensive; never blocks)."""
    from lhassa.llm.ccusage import read_ccusage
    from lhassa.llm.gateway import gateway

    config = lhassa_config.Config()
    rows = await gateway.ledger_rows()

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    today_cost = 0.0
    today_in = 0
    today_out = 0
    for r in rows:
        if str(r.get("ts") or "").startswith(today):
            today_cost += float(r.get("cost_usd") or 0.0)
            today_in += int(r.get("input_tokens") or 0)
            today_out += int(r.get("output_tokens") or 0)

    seconds_elapsed = now.hour * 3600 + now.minute * 60 + now.second
    day_fraction = max(seconds_elapsed / 86400.0, 1e-6)
    projected_today = round(today_cost / day_fraction, 6)

    return {
        "today": {
            "date": today,
            "cost_usd": round(today_cost, 6),
            "input_tokens": today_in,
            "output_tokens": today_out,
        },
        "budget": {
            "daily_token_budget": gateway.effective_daily_budget(config),
            "output_tokens_today": await gateway.daily_output_tokens(),
        },
        "projection": {
            "day_fraction_elapsed": round(day_fraction, 4),
            "projected_today_cost_usd": projected_today,
            "projected_month_cost_usd": round(projected_today * 30, 6),
        },
        "ccusage": await read_ccusage(),
    }


@router.get("/api/fleet")
async def get_fleet():
    """Known machines with their last check-in and desired_state.

    Derived from existing reads: check-in episodes (``despertar_por`` =
    ``checkin:<machine>``) carry the last-seen time; ``desired_state`` carries the
    reconciliation target per machine. A small inline query unions the two."""
    from lhassa._vendor import aiosqlite

    await db.init_db()
    inst = lhassa_config.Config.instance_id
    machines: dict[str, dict] = {}
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT despertar_por AS dp, MAX(creado_en) AS last
               FROM episodes
               WHERE instance_id = ? AND despertar_por LIKE 'checkin:%'
               GROUP BY despertar_por""",
            (inst,),
        ) as cur:
            for row in await cur.fetchall():
                mid = str(row["dp"]).split("checkin:", 1)[1]
                machines.setdefault(mid, {})["last_checkin"] = row["last"]
        async with conn.execute(
            "SELECT DISTINCT machine_id FROM desired_state WHERE instance_id = ?",
            (inst,),
        ) as cur:
            for row in await cur.fetchall():
                machines.setdefault(row["machine_id"], {})

    fleet = []
    for mid in sorted(machines):
        ds = await db.get_desired_state(inst, mid)
        fleet.append(
            {
                "machine_id": mid,
                "last_checkin": machines[mid].get("last_checkin"),
                "desired_state": ds,
            }
        )
    return fleet


# ── Overview — one defensive aggregate for the dashboard home ──────────────────
#
# A single read-only digest for the dashboard's "home" surface and ``lhassa
# status``: brains, skills, pending approvals, today's cost, health, recent
# activity, and tool states — everything the owner needs to see (and approve) at a
# glance. Each sub-section is gathered INDEPENDENTLY and DEFENSIVELY: a failing
# subsystem is reported under ``errors`` and replaced with a safe default, so the
# response is always complete and NEVER 500s. LLM-free (no provider is constructed,
# no model is called). ``build_overview`` is the in-process function the CLI's
# ``lhassa status`` reuses directly (it does not HTTP back to localhost).

async def _overview_brains() -> dict:
    """Brain count + permanent admin id + standalone? (from the org topology)."""
    from lhassa.org import management

    described = management.describe()
    return {
        "count": len(described.get("nodes", []) or []),
        "admin": described.get("admin"),
        "root": described.get("root"),
        "standalone": described.get("standalone"),
    }


async def _overview_skills() -> dict:
    """Installed skill count + recommended (offline catalog minus installed)."""
    from lhassa.skills.ai import recommend_skills
    from lhassa.skills.store import SkillStore

    installed = SkillStore(lhassa_config.Config).list_skills()
    recommended = recommend_skills(config=lhassa_config.Config)
    return {"installed": len(installed), "recommended": len(recommended)}


async def _overview_approvals() -> dict:
    """Pending approvals: a count + the queue (id, tipo, payload summary)."""
    await db.init_db()
    rows = await approval.pending(lhassa_config.Config.instance_id)
    items = []
    for r in rows:
        payload = r.get("payload") or {}
        items.append(
            {
                "id": r.get("id"),
                "tipo": r.get("tipo"),
                "accion": payload.get("accion"),
                "actor_id": payload.get("actor_id"),
                "summary": payload.get("accion") or r.get("tipo") or "?",
                "creado_en": r.get("creado_en"),
            }
        )
    return {"pending": len(items), "items": items}


async def _overview_cost() -> dict:
    """Today's spend + the daily budget + the kill-switch (the /api/llm/limits path)."""
    from lhassa.llm.gateway import gateway

    config = lhassa_config.Config()
    rows = await gateway.ledger_rows()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_cost = 0.0
    for r in rows:
        if str(r.get("ts") or "").startswith(today):
            today_cost += float(r.get("cost_usd") or 0.0)
    return {
        "today_cost_usd": round(today_cost, 6),
        "daily_token_budget": gateway.effective_daily_budget(config),
        "output_tokens_today": await gateway.daily_output_tokens(),
        "kill_switch": gateway.kill_switch,
    }


async def _overview_health() -> dict:
    """Worst level + ok, from the LLM-free PC-state checks (no notifications)."""
    from lhassa.health import checks

    digest = checks.run_all(notify=False)
    return {"worst_level": digest.get("worst_level"), "ok": digest.get("ok")}


async def _overview_activity() -> dict:
    """The last few episodes — what the brain has been doing, newest first."""
    await db.init_db()
    rows = await db.load_recent_episodes(lhassa_config.Config.instance_id, 8)
    recent = [
        {
            "id": r.get("id"),
            "creado_en": r.get("creado_en"),
            "despertar_por": r.get("despertar_por"),
            "decision": r.get("decision"),
            "resultado": r.get("resultado"),
        }
        for r in rows
    ]
    return {"recent": recent}


async def _overview_tools() -> dict:
    """Self-written tools: total + a count by estado (cuarentena/graduada/rechazada)."""
    await db.init_db()
    rows = await db.list_tools(lhassa_config.Config.instance_id)
    by_estado: dict[str, int] = {}
    for r in rows:
        estado = str(r.get("estado") or "?")
        by_estado[estado] = by_estado.get(estado, 0) + 1
    return {"count": len(rows), "by_estado": by_estado}


# ── Command-center sections (what's running now / pulse / flows / today) ───────
#
# These four power the Overview's "command center" surface. Each is cheap
# (in-memory reads of ``state.agent_runs`` or a single small DB/file read) and, like
# every other section, defensive: a failure lands in ``errors`` and the section
# falls back to its default. LLM-free.

async def _overview_now_running() -> list[dict]:
    """Agentic runs in flight RIGHT NOW (status ``running``) — an in-memory O(n) read.

    Returns the newest-first list the panel's "Now running" strip renders (each row
    gets an inline Stop → ``POST /api/code/{id}/stop``). Flows are intentionally NOT
    here: flow runs are currently SYNCHRONOUS with no long-lived "running" state, so
    there is no running-flow signal to surface — see ``_overview_flows``.
    """
    runs = [
        {
            "id": r.id,
            "name": r.name,
            "cli": r.cli,
            "task": r.task,
            "status": r.status,
            "started_at": r.started_at,
        }
        for r in state.agent_runs.values()
        if r.status == "running"
    ]
    runs.sort(key=lambda x: x["started_at"] or 0, reverse=True)
    return runs


async def _overview_pulse() -> dict:
    """Do-and-sleep pulse: last beat (latest episode ts), online/offline mode, alive.

    ``next_tick`` is deliberately OMITTED: the heartbeat cadence lives in the systemd
    timer's ``OnCalendar`` (not in ``Config``), so there is nothing to derive it from
    here and we never fabricate a time — the panel simply hides that field.
    """
    import os

    await db.init_db()
    rows = await db.load_recent_episodes(lhassa_config.Config.instance_id, 1)
    last_beat = rows[0].get("creado_en") if rows else None
    live = (os.getenv("LHASSA_ORG_LIVE", "") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    return {
        "last_beat": last_beat,
        "mode": "online" if live else "offline",
        "alive": last_beat is not None,
    }


async def _overview_flows() -> dict:
    """Flow count (from the ``FlowStore``) + the last flow-run status.

    ``last_status`` is ``None`` today: flow runs are SYNCHRONOUS and not persisted, so
    there is no stored "last run" to read. Live running-flow state / a last-run record
    would need flow-run tracking (out of scope). If that lands, source it here.
    """
    from lhassa.flows.store import FlowStore

    flows = FlowStore(lhassa_config.Config()).list_flows()
    return {"count": len(flows), "last_status": None}


async def _overview_runs_today() -> dict:
    """Today's agentic runs bucketed by terminal status (done / stopped / failed).

    "Today" is the local calendar day; ``failed`` folds the backend's ``error`` status
    and its ``failed`` alias. Cheap in-memory read of ``state.agent_runs``.
    """
    day_start = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    done = stopped = failed = 0
    for r in state.agent_runs.values():
        if (r.started_at or 0) < day_start:
            continue
        if r.status == "done":
            done += 1
        elif r.status == "stopped":
            stopped += 1
        elif r.status in ("error", "failed"):
            failed += 1
    return {"done": done, "stopped": stopped, "failed": failed}


_OVERVIEW_SECTIONS = {
    "brains": _overview_brains,
    "skills": _overview_skills,
    "approvals": _overview_approvals,
    "cost": _overview_cost,
    "health": _overview_health,
    "activity": _overview_activity,
    "tools": _overview_tools,
    "now_running": _overview_now_running,
    "pulse": _overview_pulse,
    "flows": _overview_flows,
    "runs_today": _overview_runs_today,
}

_OVERVIEW_DEFAULTS: dict[str, object] = {
    "brains": {"count": 0, "admin": None, "root": None, "standalone": None},
    "skills": {"installed": 0, "recommended": 0},
    "approvals": {"pending": 0, "items": []},
    "cost": {
        "today_cost_usd": 0.0,
        "daily_token_budget": 0,
        "output_tokens_today": 0,
        "kill_switch": False,
    },
    "health": {"worst_level": "info", "ok": True},
    "activity": {"recent": []},
    "tools": {"count": 0, "by_estado": {}},
    "now_running": [],
    "pulse": {"last_beat": None, "mode": "offline", "alive": False},
    "flows": {"count": 0, "last_status": None},
    "runs_today": {"done": 0, "stopped": 0, "failed": 0},
}


async def build_overview() -> dict:
    """Assemble the dashboard-home aggregate, section by section, defensively.

    Returns one mapping with a key per section plus ``errors`` (a section→message
    map of whatever degraded) and a little envelope (``instance_id``,
    ``generated_at``). A single failing subsystem is reported, never fatal — the
    response is always complete. LLM-free; reused in-process by ``lhassa status``.
    """
    sections: dict = {}
    errors: dict[str, str] = {}
    for name, fn in _OVERVIEW_SECTIONS.items():
        try:
            sections[name] = await fn()
        except Exception as exc:  # one degraded subsystem is reported, not fatal
            errors[name] = f"{type(exc).__name__}: {exc}"
            default = _OVERVIEW_DEFAULTS[name]
            # Copy so a caller can't mutate the shared default; handle list sections
            # (e.g. ``now_running``) as well as the dict ones.
            sections[name] = list(default) if isinstance(default, list) else dict(default)
    sections["errors"] = errors
    sections["instance_id"] = lhassa_config.Config.instance_id
    sections["generated_at"] = int(time.time())
    return sections


@router.get("/api/overview")
async def get_overview():
    """One defensive aggregate for the dashboard home / ``lhassa status``.

    Sections: ``brains``, ``skills``, ``approvals`` (with the pending list so the
    owner can approve from here), ``cost`` (today + budget + kill-switch),
    ``health`` (worst level + ok), ``activity`` (recent episodes) and ``tools``
    (count by estado). Each is gathered independently; any failure lands in
    ``errors`` instead of taking the response down. Never 500s; LLM-free.
    """
    return await build_overview()
