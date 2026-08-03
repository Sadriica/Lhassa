"""Overview routes — one defensive aggregate for the dashboard home.

A single read-only digest for the dashboard's "home" surface and ``lhassa
status``: brains, skills, pending approvals, today's cost, health, recent
activity, and tool states — everything the owner needs to see (and approve) at a
glance. Each sub-section is gathered INDEPENDENTLY and DEFENSIVELY: a failing
subsystem is reported under ``errors`` and replaced with a safe default, so the
response is always complete and NEVER 500s. LLM-free (no provider is constructed,
no model is called). ``build_overview`` is the in-process function the CLI's
``lhassa status`` reuses directly (it does not HTTP back to localhost).

``Config`` is read through the ``lhassa.config`` MODULE (``lhassa_config.Config``)
rather than a bound name, so a config reload (some endpoint tests reload it) is
picked up live — the same seam ``runtime_routes`` uses.

Mounted by the integrator with ``app.include_router(overview_routes.router)``.
"""

import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

import lhassa.config as lhassa_config
from lhassa.core import db
from lhassa.security import approval
from lhassa.state import state

router = APIRouter()


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
        desc = approval.describe(r)
        items.append(
            {
                "id": r.get("id"),
                "tipo": r.get("tipo"),
                "accion": payload.get("accion"),
                "actor_id": payload.get("actor_id"),
                "summary": desc["title"],
                "detail": desc.get("detail") or "",
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


async def _overview_integrity() -> dict:
    """Latest executable-contract status and fresh-attestation verdict."""
    from lhassa.core.conformance import attestation

    await db.init_db()
    verdict = await attestation(lhassa_config.Config.instance_id)
    run = verdict.get("run") or {}
    return {
        "valid": verdict["valid"],
        "reason": verdict["reason"],
        "status": run.get("status"),
        "run_id": run.get("id"),
        "attestation_expires_at": run.get("attestation_expires_at"),
        "findings_warn": run.get("findings_warn", 0),
        "findings_critical": run.get("findings_critical", 0),
    }


async def _overview_activity() -> dict:
    """The last few episodes — what the brain has been doing, newest first."""
    await db.init_db()
    rows = await db.load_recent_episodes(lhassa_config.Config.instance_id, 8)
    total = await db.count_episodes(lhassa_config.Config.instance_id)
    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    last_24h = await db.count_episodes_since(lhassa_config.Config.instance_id, since_24h)
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
    return {
        "recent": recent,
        "total": total,
        "last_24h": last_24h,
        "recent_limit": 8,
        "window": "24h",
    }


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


async def _overview_personal() -> dict:
    """Personal secretary snapshot: priorities, last check-in and rendered agenda."""
    from lhassa.personal import agenda as personal_agenda

    cfg = lhassa_config.Config()
    summary = await personal_agenda.build(cfg, slot="today")
    return {
        "priorities": summary.priorities,
        "latest_checkin": summary.latest_checkin,
        "pending_tasks": [t.__dict__ for t in summary.pending_tasks[:5]],
        "approvals": summary.approvals[:5],
        "text": personal_agenda.render(
            summary,
            language=getattr(cfg, "digest_language", "") or "en",
        ),
    }


_OVERVIEW_SECTIONS = {
    "brains": _overview_brains,
    "skills": _overview_skills,
    "approvals": _overview_approvals,
    "cost": _overview_cost,
    "health": _overview_health,
    "integrity": _overview_integrity,
    "activity": _overview_activity,
    "tools": _overview_tools,
    "now_running": _overview_now_running,
    "pulse": _overview_pulse,
    "flows": _overview_flows,
    "runs_today": _overview_runs_today,
    "personal": _overview_personal,
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
    "integrity": {
        "valid": False,
        "reason": "unavailable",
        "status": None,
        "run_id": None,
        "attestation_expires_at": None,
        "findings_warn": 0,
        "findings_critical": 0,
    },
    "activity": {"recent": [], "total": 0, "last_24h": 0, "recent_limit": 8, "window": "24h"},
    "tools": {"count": 0, "by_estado": {}},
    "now_running": [],
    "pulse": {"last_beat": None, "mode": "offline", "alive": False},
    "flows": {"count": 0, "last_status": None},
    "runs_today": {"done": 0, "stopped": 0, "failed": 0},
    "personal": {"priorities": [], "latest_checkin": "", "pending_tasks": [], "approvals": [], "text": ""},
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
