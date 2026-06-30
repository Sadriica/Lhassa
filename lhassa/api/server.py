"""
FastAPI server — REST API + WebSocket fan-out + React static file serving.
Runs at http://127.0.0.1:7766 by default (configurable via LHASSA_API_HOST / LHASSA_API_PORT).
"""

import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
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
from lhassa.security import approval, signing
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
    elif outcome == "pendiente_aprobacion":
        # Parked at the approval frontier (§2.6): not executed, not failed. It
        # waits in the approvals queue for a human OK (do-and-sleep, §2.1).
        estado = "esperando_aprobacion"
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


# ── Approval frontier — the HITL queue (§2.6, §2.9-(1)) ──────────────────────────
#
# The dashboard's first panel is "la cola de aprobaciones". GET returns the pending
# queue; POST resolves one approval and feeds the autonomy knob (a repeatedly
# approved human action earns auto-approval; a denial resets its streak). HITL is
# per-flow: the threshold lives per action by risk, never one global knob.

@app.get("/api/approvals")
async def get_approvals():
    """The pending approval queue for this instance (oldest first)."""
    await db.init_db()
    return await approval.pending(Config.instance_id)


@app.post("/api/approvals/{approval_id}")
async def resolve_approval_endpoint(approval_id: str, payload: dict):
    """Resolve a pending approval. Body: ``{approved: bool, by_actor?: str}``.

    Marks the approval aprobada/rechazada and feeds the autonomy knob so a
    repeatedly approved human action earns auto-approval (denials reset the streak).
    """
    if not isinstance(payload, dict) or "approved" not in payload:
        raise HTTPException(status_code=400, detail="'approved' (bool) is required.")
    approved = payload["approved"]
    if not isinstance(approved, bool):
        raise HTTPException(status_code=400, detail="'approved' must be a boolean.")
    by_actor = (payload.get("by_actor") or "duenio").strip() or "duenio"

    await db.init_db()
    await approval.resolve(
        Config.instance_id, approval_id, approved=approved, by_actor=by_actor
    )
    return {"ok": True, "approval_id": approval_id, "approved": approved}


# ── Quarantine room (§2.9-(2)) — the self-written tools and their gate state ──
# GET lists the tools table (the quarantine-room data: name/domain/estado/metrics/
# usage). POST graduates a candidate: "aprobación = merge" — it routes a
# tool_graduation approval through the approval queue and merges the candidate
# branch into main via the local-git versioning layer. Offline; no GitHub token.

@app.get("/api/tools")
async def get_tools():
    """The quarantine room: every tool with its estado + gate/metric/usage info."""
    await db.init_db()
    rows = await db.list_tools(Config.instance_id)
    return [
        {
            "id": r.get("id"),
            "nombre": r.get("nombre"),
            "dominio": r.get("dominio"),
            "estado": r.get("estado"),
            "metrica_baseline": r.get("metrica_baseline"),
            "metrica_actual": r.get("metrica_actual"),
            "usos": r.get("usos"),
            "fallos": r.get("fallos"),
            "creado_en": r.get("creado_en"),
        }
        for r in rows
    ]


@app.post("/api/tools/{nombre}/graduate")
async def graduate_tool(nombre: str, payload: dict | None = None):
    """Approve a tool's graduation: aprobación = merge.

    Body (all optional): ``{branch?, approval_id?, by_actor?}``. Routes a
    ``tool_graduation`` approval through the approval queue and, on approve, merges
    the candidate branch into main. Marks the tool ``graduada``. Returns the merge
    commit (empty when git is unavailable — the approval still resolves).
    """
    from lhassa.agents import versioning

    payload = payload or {}
    branch = payload.get("branch")
    approval_id = payload.get("approval_id")
    by_actor = (payload.get("by_actor") or "duenio").strip() or "duenio"

    await db.init_db()
    merge_commit = await versioning.approve(
        nombre,
        branch=branch,
        instance_id=Config.instance_id,
        approval_id=approval_id,
        by_actor=by_actor,
    )
    await db.upsert_tool(Config.instance_id, nombre, estado="graduada")
    return {"ok": True, "tool": nombre, "estado": "graduada", "merge_commit": merge_commit}


# ── Phase 6: reports / projections / fleet (read-only, additive) ─────────────────
#
# Rich dashboard surfaces for "madurez": what the brain has been doing (reports),
# where cost is trending (projections, off the same ledger/ccusage as
# /api/llm/*), and the multi-machine fleet (last check-in + desired_state). All
# read-only and instance-scoped, matching the existing endpoints' await style.

@app.get("/api/reports")
async def get_reports(n: int = Query(default=50, ge=1, le=500)):
    """Recent episodes + an outcomes summary (counts by wake trigger / decision)."""
    await db.init_db()
    rows = await db.load_recent_episodes(Config.instance_id, n)

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


@app.get("/api/projections")
async def get_projections():
    """Cost projection from the gateway ledger (same data as /api/llm/*).

    Spend so far today + a simple linear projection to day/month end. Pricing is
    NOT duplicated — the ledger already carries per-call ``cost_usd``. The
    cross-harness ccusage read is included (defensive; never blocks)."""
    from lhassa.llm.ccusage import read_ccusage
    from lhassa.llm.gateway import gateway

    config = Config()
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


@app.get("/api/fleet")
async def get_fleet():
    """Known machines with their last check-in and desired_state.

    Derived from existing reads: check-in episodes (``despertar_por`` =
    ``checkin:<machine>``) carry the last-seen time; ``desired_state`` carries the
    reconciliation target per machine. A small inline query unions the two."""
    import aiosqlite

    await db.init_db()
    inst = Config.instance_id
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


# ── Org MANAGEMENT — create brains, skills, limits, hierarchy (additive) ─────────
#
# The backend the dashboard org panel (a later slice) drives: read the chart +
# available skills, create/update/remove brains, re-parent ("who commands whom"),
# and promote to root ("who commands everyone"). All instance-scoped and
# DEFENSIVE — a bad body / invalid op returns a clear 4xx, never a 500, and never
# corrupts the topology file (management validates BEFORE it persists). These
# routes are purely additive; the existing routes and the websocket are untouched.


def _org_4xx(exc: Exception) -> HTTPException:
    """Map a management/topology error to a 4xx (404 for a missing brain, else 400)."""
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc).strip("'\""))
    return HTTPException(status_code=400, detail=str(exc))


@app.get("/api/org/topology")
async def get_org_topology():
    """The full org chart: nodes + hierarchy + skills + limits (``describe``)."""
    from lhassa.org import management

    return management.describe()


@app.get("/api/org/skills")
async def get_org_skills():
    """The skills that CAN be assigned — the specialist registry's domains."""
    from lhassa.org import management

    return management.available_skills()


@app.get("/api/org/route")
async def get_org_route(goal: str = Query(..., min_length=1)):
    """Preview the root→leaf routing path a goal would take (read-only, no LLM)."""
    from lhassa.org import topology as topo
    from lhassa.org.coordinator import route_in_topology

    topology = topo.load_topology()
    node = topology.root()
    path = [node.id]
    while not topology.is_leaf(node.id):
        child_id = route_in_topology(topology, node.id, goal)
        path.append(child_id)
        node = topology.node(child_id)
    leaf = topology.node(path[-1])
    return {
        "goal": goal,
        "path": path,
        "leaf": leaf.id,
        "department": leaf.department or leaf.id,
    }


@app.post("/api/org/brains")
async def create_org_brain(payload: dict):
    """Create a brain. Body: ``{id, role?, parent?, department?, skills?, limits?}``.

    Returns the updated chart (``describe``). A duplicate id / missing parent /
    cycle returns 400 with a reason; the file is never partially written.
    """
    from lhassa.org import management

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="brain payload must be a JSON object.")
    node_id = (payload.get("id") or "").strip()
    if not node_id:
        raise HTTPException(status_code=400, detail="'id' is required.")
    skills = payload.get("skills") or ()
    if not isinstance(skills, (list, tuple)):
        raise HTTPException(status_code=400, detail="'skills' must be an array.")
    limits = payload.get("limits") or {}
    if not isinstance(limits, dict):
        raise HTTPException(status_code=400, detail="'limits' must be an object.")
    try:
        management.add_brain(
            node_id,
            role=payload.get("role") or "department",
            parent=payload.get("parent") or None,
            department=payload.get("department") or None,
            skills=list(skills),
            limits=limits,
        )
    except Exception as exc:  # ManagementError/TopologyError (ValueError) → 4xx
        raise _org_4xx(exc) from exc
    return management.describe()


@app.patch("/api/org/brains/{brain_id}")
async def update_org_brain(brain_id: str, payload: dict):
    """Update a brain. Body (all optional): ``{skills?, limits?, role?, department?}``.

    A ``None``/absent field is left unchanged. Returns the updated chart.
    """
    from lhassa.org import management

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="update payload must be a JSON object.")
    skills = payload.get("skills")
    if skills is not None and not isinstance(skills, (list, tuple)):
        raise HTTPException(status_code=400, detail="'skills' must be an array.")
    limits = payload.get("limits")
    if limits is not None and not isinstance(limits, dict):
        raise HTTPException(status_code=400, detail="'limits' must be an object.")
    try:
        management.update_brain(
            brain_id,
            skills=list(skills) if skills is not None else None,
            limits=limits,
            role=payload.get("role"),
            department=payload.get("department"),
        )
    except Exception as exc:
        raise _org_4xx(exc) from exc
    return management.describe()


@app.delete("/api/org/brains/{brain_id}")
async def delete_org_brain(brain_id: str, force: bool = Query(default=False)):
    """Remove a brain (``?force=true`` removes its whole subtree). Returns the chart."""
    from lhassa.org import management

    try:
        management.remove_brain(brain_id, force=force)
    except Exception as exc:
        raise _org_4xx(exc) from exc
    return management.describe()


@app.post("/api/org/brains/{brain_id}/move")
async def move_org_brain(brain_id: str, payload: dict):
    """Re-parent a brain (who commands whom). Body: ``{parent}``. Returns the chart."""
    from lhassa.org import management

    if not isinstance(payload, dict) or not (payload.get("parent") or "").strip():
        raise HTTPException(status_code=400, detail="'parent' is required.")
    try:
        management.set_parent(brain_id, payload["parent"].strip())
    except Exception as exc:
        raise _org_4xx(exc) from exc
    return management.describe()


@app.post("/api/org/brains/{brain_id}/promote")
async def promote_org_brain(brain_id: str):
    """Promote a brain to root (it commands everyone). Returns the updated chart."""
    from lhassa.org import management

    try:
        management.set_root(brain_id)
    except Exception as exc:
        raise _org_4xx(exc) from exc
    return management.describe()


# ── Skills (first-class; SKILL.md) ────────────────────────────────────────────
# CRUD + AI create/improve + offline recommendations over the SkillStore
# (~/.lhassa/skills), plus which brains carry a skill. Defensive: bad body → 400,
# missing skill → 404, AI degrades to a clear error when no model is configured.
# Static sub-paths (recommend) are declared before the `{name}` routes so they win.

def _skill_store():
    from lhassa.skills.store import SkillStore

    return SkillStore(Config)


@app.get("/api/skills")
async def list_skills_endpoint():
    """Every installed skill (SKILL.md) as JSON."""
    from lhassa.skills.skillmd import skill_to_dict

    return [skill_to_dict(s) for s in _skill_store().list_skills()]


@app.get("/api/skills/recommend")
async def recommend_skills_endpoint(context: str = Query(default="")):
    """Recommended skills — the offline catalog minus what's installed."""
    from lhassa.skills.ai import recommend_skills

    return recommend_skills(context or None, config=Config)


@app.post("/api/skills")
async def create_skill_endpoint(payload: dict):
    """Create a skill from ``{name, description?, instructions?, metadata?}``."""
    from lhassa.skills.skillmd import skill_from_dict, skill_to_dict

    if not isinstance(payload, dict) or not (payload.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="'name' is required.")
    try:
        skill = skill_from_dict(payload)
        _skill_store().create(skill)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill_to_dict(skill)


@app.post("/api/skills/ai")
async def create_skill_ai_endpoint(payload: dict):
    """Draft a skill WITH AI from ``{brief}`` and save it (degrades without a model)."""
    from lhassa.skills.ai import create_skill_with_ai
    from lhassa.skills.skillmd import skill_to_dict

    brief = (payload or {}).get("brief") if isinstance(payload, dict) else None
    if not brief:
        raise HTTPException(status_code=400, detail="'brief' is required.")
    skill = await create_skill_with_ai(brief, config=Config)
    saved = False
    if not skill.error:
        try:
            _skill_store().save(skill)
            saved = True
        except Exception as exc:  # persisting must not fail the request
            logger.warning("skills: could not save AI-drafted skill: {}", exc)
    return {"skill": skill_to_dict(skill), "saved": saved, "error": skill.error}


@app.get("/api/skills/{name}")
async def get_skill_endpoint(name: str):
    from lhassa.skills.skillmd import skill_to_dict

    skill = _skill_store().get(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"skill {name!r} not found.")
    return skill_to_dict(skill)


@app.get("/api/skills/{name}/brains")
async def skill_brains_endpoint(name: str):
    """Which brains carry this skill."""
    from lhassa.skills.store import brains_with_skill

    return {"skill": name, "brains": brains_with_skill(name, Config)}


@app.post("/api/skills/{name}/improve")
async def improve_skill_endpoint(name: str):
    """Improve a skill WITH AI and save it (degrades without a model)."""
    from lhassa.skills.ai import improve_skill_with_ai
    from lhassa.skills.skillmd import skill_to_dict

    skill = await improve_skill_with_ai(name, config=Config)
    saved = False
    if not skill.error:
        try:
            _skill_store().save(skill)
            saved = True
        except Exception as exc:
            logger.warning("skills: could not save improved skill: {}", exc)
    return {"skill": skill_to_dict(skill), "saved": saved, "error": skill.error}


@app.patch("/api/skills/{name}")
async def update_skill_endpoint(name: str, payload: dict):
    """Overwrite a skill from a JSON body. Returns the saved skill."""
    from lhassa.skills.skillmd import skill_from_dict, skill_to_dict

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object.")
    data = dict(payload)
    data.setdefault("name", name)
    try:
        skill = skill_from_dict(data)
        _skill_store().save(skill)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill_to_dict(skill)


@app.delete("/api/skills/{name}")
async def delete_skill_endpoint(name: str):
    if not _skill_store().delete(name):
        raise HTTPException(status_code=404, detail=f"skill {name!r} not found.")
    return {"ok": True, "deleted": name}


@app.post("/api/skills/{name}/assign")
async def assign_skill_endpoint(name: str, payload: dict):
    """Assign a skill to a brain. Body ``{brain_id}``."""
    from lhassa.skills.store import assign_to_brain

    brain_id = (payload or {}).get("brain_id") if isinstance(payload, dict) else None
    if not brain_id:
        raise HTTPException(status_code=400, detail="'brain_id' is required.")
    try:
        assign_to_brain(name, brain_id, Config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "skill": name, "brain": brain_id}


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
