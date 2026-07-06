"""The environment MONITOR & continuous-improvement SUPERVISOR (Fase 7).

A brain can be more than an executor: it can be the one **everyone reports to** —
the brain that constantly watches its environment (episodes, health, flow runs,
pending approvals, failures) and turns what it sees into concrete, routed
improvements. This module is that capability, built as a REUSABLE seam so ANY
brain can be made a monitor; the interns brain is then configured to embody it.

Two design rules mirror :mod:`lhassa.org.executor` exactly:

  * **Offline-testable, $0 by default.** The live reasoning pass is gated behind
    ``LHASSA_ORG_LIVE`` (default OFF). With the flag off, :func:`monitor_pass`
    returns a DETERMINISTIC structured report built purely from the gathered
    signals and never touches a model. A live pass (flag on) hands the signals to
    the supervising brain through the org think path
    (:func:`lhassa.org.executor.brain_executor` + the ``monitor`` skill).
  * **A monitoring failure is data, never a crash.** Every signal source is read
    in its own ``try/except`` and degrades to empty; :func:`monitor_pass` never
    raises — an unexpected error degrades to a report carrying an ``error`` note.

Where a heartbeat would plug in: a routine beat (``core/heartbeat.py``) could call
:func:`monitor_pass` on a cadence and surface the report through ``notify``. That
hook is intentionally OUT of scope here — this module only provides the capability.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import db
from lhassa.core.episode import Episode

# Truthy values that turn the live reasoning path on (default OFF → offline + $0),
# identical to the executor's gate so the whole org layer flips with one flag.
_TRUTHY = {"1", "true", "yes", "on"}

# The supervision skill: a NAME on the monitor brain → a SKILL.md in the store →
# loaded into the prompt at think time (the 3-layer skills convention).
MONITOR_SKILL_NAME = "monitor"

# The role/description the monitor brain wears in the topology (open label — RBAC is
# by coordinating-vs-executing, not by this string, so renaming a role is safe).
MONITOR_ROLE = "monitor"
MONITOR_DESCRIPTION = (
    "environment monitor & continuous-improvement supervisor — everyone reports to it"
)

_MONITOR_INSTRUCTIONS = (
    "You are the environment's supervisor. Everyone reports to you. Continuously "
    "review recent activity, health and outcomes; surface anomalies and RISKS, and "
    "propose CONCRETE, prioritized improvements; route each fix to the right "
    "department. Be specific and terse.\n\n"
    "You are given a compact snapshot of recent signals (episodes, health digest, "
    "work-flows, pending approvals, recent failures). Reason over it and answer with "
    "a SINGLE JSON object and nothing else, shaped exactly like:\n\n"
    "{\n"
    '  "observations": ["..."],   // what is happening, terse\n'
    '  "risks": ["..."],          // anomalies / risks, worst first\n'
    '  "improvements": ["..."],   // concrete, prioritized fixes\n'
    '  "delegations": [ {"department": "<name>", "fix": "<what to do>"} ]\n'
    "}\n\n"
    "Every improvement should map to a delegation routing the fix to the department "
    "best placed to handle it. Keep each list short and high-signal."
)

# How many recent episodes to review by default (cheap; newest first).
_DEFAULT_EPISODES = 20

# Substrings in an episode that mark a failure / offline / deferred outcome.
_FAILURE_MARKERS = (
    "error",
    "errored",
    "deferred",
    "offline",
    "could not",
    "failed",
    "fail",
    "timeout",
    "denied",
)


def _live_enabled(config: Config) -> bool:
    """Whether the live reasoning path is on (``LHASSA_ORG_LIVE``; default OFF)."""
    return (os.getenv("LHASSA_ORG_LIVE", "") or "").strip().lower() in _TRUTHY


def _resolve_config(config: "Optional[Config]") -> Config:
    return config if config is not None else Config()


# ── signal collection (read-only, cheap, defensive — never an LLM) ────────────────


def _read_episodes(instance_id: str, n: int) -> list[dict]:
    """Most-recent ``n`` episodes via a read-only sqlite connection. Never raises.

    A synchronous read (not the async ``db`` API) so :func:`gather_signals` can be a
    plain function callable from inside or outside an event loop. WAL lets this
    reader run freely against the append-only log. A missing DB/table → ``[]``.
    """
    path = getattr(db, "DB_PATH", None)
    if not path or not Path(path).exists():
        return []
    try:
        con = sqlite3.connect(str(path))
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute(
                """SELECT id, despertar_por, decision, accion, resultado,
                          costo_usd, creado_en
                     FROM episodes
                    WHERE instance_id = ?
                    ORDER BY creado_en DESC, rowid DESC
                    LIMIT ?""",
                (instance_id, n),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("monitor: could not read episodes — {}", exc)
        return []


def _read_pending_approvals(instance_id: str) -> list[dict]:
    """Pending approvals via a read-only sqlite connection. Never raises."""
    path = getattr(db, "DB_PATH", None)
    if not path or not Path(path).exists():
        return []
    try:
        con = sqlite3.connect(str(path))
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute(
                """SELECT id, tipo, estado, creado_en
                     FROM approvals
                    WHERE instance_id = ? AND estado = 'pendiente'
                    ORDER BY creado_en""",
                (instance_id,),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("monitor: could not read approvals — {}", exc)
        return []


def _dept_from_episode(ep: dict) -> Optional[str]:
    """Extract a ``dept:<name>`` department hint from an episode's action, if any."""
    accion = str(ep.get("accion") or "")
    marker = "dept:"
    idx = accion.find(marker)
    if idx == -1:
        return None
    tail = accion[idx + len(marker):]
    name = tail.split()[0].strip() if tail.split() else ""
    return name or None


def _is_stale(ep: dict, max_age_hours: float) -> bool:
    """Whether ``ep`` is older than the freshness window (0 → never stale).

    An unparseable/missing ``creado_en`` counts as FRESH — defensively keep the
    episode rather than silently dropping a real failure we can't date.
    """
    if max_age_hours <= 0:
        return False
    ts_str = str(ep.get("creado_en") or "")
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - ts
    return age.total_seconds() > max_age_hours * 3600


def _failures_from_episodes(
    episodes: list[dict], *, max_age_hours: float = 0.0
) -> list[dict]:
    """Recent episodes whose outcome reads as a failure / offline / deferred marker.

    Three noise guards keep the signal honest:

    - the monitor's OWN episodes (``despertar_por == "monitor"``) never count —
      their summaries quote failure counts ("N new failure(s)"), so counting them
      would have the watcher alerting on its own reflection.
    - a completed think's ``resultado`` is model PROSE (a CV, an analysis…) where
      "error"/"fail" are content, not outcome — for ``think*`` decisions the
      markers match on ``decision``/``accion`` only. A think that actually failed
      carries the marker in its decision ("telegram: error", "org: deferred").
    - a failure only counts while FRESH (``max_age_hours``; 0 = no cutoff): on a
      quiet instance old failures would otherwise sit in the last-N episode
      window for days and keep inflating every "total N failure(s)".
    """
    out: list[dict] = []
    for ep in episodes:
        if (ep.get("despertar_por") or "") == "monitor":
            continue
        if _is_stale(ep, max_age_hours):
            continue
        keys = ("decision", "accion")
        if not str(ep.get("decision") or "").lower().startswith("think"):
            keys = ("decision", "accion", "resultado")
        blob = " ".join(str(ep.get(k) or "") for k in keys).lower()
        if any(marker in blob for marker in _FAILURE_MARKERS):
            out.append(
                {
                    "id": ep.get("id"),
                    "department": _dept_from_episode(ep),
                    "decision": ep.get("decision"),
                    "accion": ep.get("accion"),
                    "resultado": ep.get("resultado"),
                    "creado_en": ep.get("creado_en"),
                }
            )
    return out


def _health_dir(config: Config) -> Path:
    """Where the health digest snapshot lives (config override → default home)."""
    override = (getattr(config, "health_dir", "") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".lhassa" / "health"


def _health_signal(config: Config) -> dict:
    """The last health digest snapshot, reduced to worst level + failing checks.

    Read-only: reads ``<health_dir>/digest.json`` written by ``health.run_all``; it
    does NOT re-run the checks (cheap + never triggers a notification). Missing/
    unreadable snapshot → a clean ``info`` digest. Never raises.
    """
    empty = {"worst_level": "info", "ok": True, "failing": [], "alerted": []}
    try:
        path = _health_dir(config) / "digest.json"
        if not path.exists():
            return empty
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(raw, dict):
            return empty
        results = raw.get("results") or {}
        failing = [
            name
            for name, r in results.items()
            if isinstance(r, dict) and r.get("level") in ("warn", "alert")
        ]
        return {
            "worst_level": raw.get("worst_level", "info"),
            "ok": bool(raw.get("ok", True)),
            "failing": failing,
            "alerted": list(raw.get("alerted") or []),
        }
    except Exception as exc:  # pragma: no cover - reads degrade already
        logger.warning("monitor: could not read health digest — {}", exc)
        return empty


def _flows_signal(config: Config) -> list[dict]:
    """The stored work-flows, reduced to ``{id, name, nodes}``. Never raises."""
    try:
        from lhassa.flows.store import FlowStore

        flows = FlowStore(config).list_flows()
        return [
            {
                "id": f.id,
                "name": getattr(f, "name", "") or f.id,
                "nodes": len(getattr(f, "nodes", []) or []),
            }
            for f in flows
        ]
    except Exception as exc:
        logger.warning("monitor: could not read flows — {}", exc)
        return []


def gather_signals(config: Config, *, instance_id: Optional[str] = None) -> dict:
    """Collect the environment's recent signals WITHOUT any LLM.

    Cheap, read-only and defensive: each source is read in its own ``try/except`` and
    a missing/unreadable source degrades to empty, never raising. This is the
    "everyone reports to it" aggregator — the raw material a monitor reasons over.

    Returns a dict with the keys:

      - ``episodes``  — the most-recent activity trail (newest first).
      - ``health``    — ``{worst_level, ok, failing, alerted}`` from the last digest.
      - ``flows``     — the defined work-flows (``{id, name, nodes}``).
      - ``approvals`` — the pending human-in-the-loop approvals.
      - ``failures``  — recent episodes whose outcome marks a failure/offline/deferred.
      - ``instance_id`` — the instance the signals belong to.
    """
    config = _resolve_config(config)
    instance_id = (
        instance_id or getattr(config, "instance_id", None) or Config.instance_id
    )

    episodes = _read_episodes(instance_id, _DEFAULT_EPISODES)
    return {
        "instance_id": instance_id,
        "episodes": episodes,
        "health": _health_signal(config),
        "flows": _flows_signal(config),
        "approvals": _read_pending_approvals(instance_id),
        "failures": _failures_from_episodes(
            episodes,
            max_age_hours=getattr(config, "monitor_failure_window_h", 24.0),
        ),
    }


# ── the offline (deterministic, $0) report ────────────────────────────────────────


def _offline_report(signals: dict, department: str) -> dict:
    """Build a structured report from the signals alone — no model, deterministic.

    The shape is the contract both paths return:
    ``{observations, risks, improvements, delegations}`` (plus metadata). Each risk
    yields a concrete improvement and a delegation routing the fix to a department.
    """
    episodes = signals.get("episodes") or []
    health = signals.get("health") or {}
    flows = signals.get("flows") or []
    approvals = signals.get("approvals") or []
    failures = signals.get("failures") or []

    observations = [
        f"I reviewed {len(episodes)} recent episode(s).",
        f"My health worst level is {health.get('worst_level', 'info')} "
        f"(ok={bool(health.get('ok', True))}).",
        f"I know {len(flows)} work-flow(s).",
        f"{len(approvals)} approval(s) are waiting.",
        f"{len(failures)} recent failure/offline marker(s) need attention.",
    ]

    risks: list[str] = []
    improvements: list[str] = []
    delegations: list[dict] = []

    failing = health.get("failing") or []
    if not health.get("ok", True) and failing:
        risks.append(
            f"My health is degraded ({health.get('worst_level', 'warn')}): "
            f"{', '.join(failing)}."
        )
        improvements.append(f"Investigate failing health checks: {', '.join(failing)}.")
        delegations.append(
            {
                "department": "operations",
                "fix": f"Resolve failing health checks: {', '.join(failing)}.",
            }
        )

    if failures:
        risks.append(f"I found {len(failures)} recent failure/offline episode(s).")
        improvements.append(
            "Triage the recent failures; add a verifier or fallback where they recur."
        )
        depts = sorted({(f.get("department") or department) for f in failures})
        for dept in depts:
            delegations.append(
                {
                    "department": dept,
                    "fix": "Review and fix the recent failures traced to this department.",
                }
            )

    if approvals:
        risks.append(f"{len(approvals)} action(s) are parked awaiting human approval.")
        improvements.append(
            f"Clear the approval queue ({len(approvals)} pending) so parked work proceeds."
        )
        delegations.append(
            {
                "department": "general",
                "fix": f"Resolve the {len(approvals)} pending approval(s).",
            }
        )

    if not risks:
        observations.append("No anomalies surfaced in the reviewed window.")
        improvements.append("Keep watch; no action is required this pass.")

    return {
        "department": department,
        "observations": observations,
        "risks": risks,
        "improvements": improvements,
        "delegations": delegations,
        "live": False,
        "model": "offline",
        "cost_usd": 0.0,
        "signals": signals,
    }


# ── the live (LHASSA_ORG_LIVE) reasoning pass ─────────────────────────────────────


def _compact_signals(signals: dict) -> str:
    """A compact, model-friendly text of the signals for the live prompt."""
    health = signals.get("health") or {}
    lines = [
        f"episodes_reviewed: {len(signals.get('episodes') or [])}",
        f"health_worst_level: {health.get('worst_level', 'info')}",
        f"health_failing: {', '.join(health.get('failing') or []) or 'none'}",
        f"flows_defined: {len(signals.get('flows') or [])}",
        f"approvals_pending: {len(signals.get('approvals') or [])}",
        f"recent_failures: {len(signals.get('failures') or [])}",
    ]
    failures = signals.get("failures") or []
    for f in failures[:8]:
        dept = f.get("department") or "?"
        lines.append(f"  failure[dept={dept}]: {str(f.get('resultado') or '')[:160]}")
    return "\n".join(lines)


def _parse_report(text: str) -> Optional[dict]:
    """Parse a model reply into the report dict, or ``None`` if it is not JSON.

    Tolerant: unwraps a single ```` ``` ```` fence and finds the first ``{...}`` span.
    Never raises.
    """
    if not (text or "").strip():
        return None
    body = text.strip()
    if body.startswith("```"):
        with contextlib.suppress(Exception):
            from lhassa.skills.skillmd import strip_code_fence

            body = strip_code_fence(body).strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    with contextlib.suppress(Exception):
        obj = json.loads(body[start : end + 1])
        if isinstance(obj, dict):
            return obj
    return None


async def _live_report(
    signals: dict,
    *,
    department: str,
    instance_id: str,
    config: Config,
    skill_name: str,
) -> dict:
    """Have the supervising brain reason over the signals via the org think path.

    Reuses :func:`lhassa.org.executor.brain_executor` with the department identity +
    the monitor skill. Parses the reply into the structured report; a non-JSON reply
    degrades to the deterministic scaffold with the free text folded into
    ``observations`` (never raises, never drops the pass).
    """
    from lhassa.org.executor import brain_executor

    goal = (
        "Review this environment snapshot and report as instructed by your monitor "
        "skill. Snapshot:\n\n" + _compact_signals(signals)
    )
    res = await brain_executor(
        goal,
        department=department,
        instance_id=instance_id,
        config=config,
        skills=[skill_name],
    )
    summary = str(res.get("summary") or "")
    parsed = _parse_report(summary)
    if parsed is None:
        report = _offline_report(signals, department)
        report["observations"] = [summary] + report["observations"] if summary else report["observations"]
        report["live"] = True
        report["model"] = res.get("model") or ""
        report["cost_usd"] = float(res.get("cost_usd") or 0.0)
        report["note"] = "model reply was not structured JSON; folded into observations"
        return report
    return {
        "department": department,
        "observations": list(parsed.get("observations") or []),
        "risks": list(parsed.get("risks") or []),
        "improvements": list(parsed.get("improvements") or []),
        "delegations": list(parsed.get("delegations") or []),
        "live": True,
        "model": res.get("model") or "",
        "cost_usd": float(res.get("cost_usd") or 0.0),
        "signals": signals,
    }


async def monitor_pass(
    *,
    department: str = "interns",
    config: Optional[Config] = None,
    instance_id: Optional[str] = None,
    skill_name: str = MONITOR_SKILL_NAME,
) -> dict:
    """Run one monitoring pass and return a structured report.

    Gathers the environment's signals (:func:`gather_signals`), then — offline by
    default — builds a deterministic ``{observations, risks, improvements,
    delegations}`` report from them at $0 with NO model call. With
    ``LHASSA_ORG_LIVE`` on, the supervising ``department`` brain reasons over the
    signals through the org think path (the ``monitor`` skill in its prompt).

    Always logs an ``Episode`` (``despertar_por="monitor"``). NEVER raises — an
    unexpected error degrades to a report carrying an ``error`` note.
    """
    config = _resolve_config(config)
    instance_id = (
        instance_id or getattr(config, "instance_id", None) or Config.instance_id
    )

    # Idempotent — the monitor may run before the DB is initialised.
    await db.init_db()

    ep = Episode.begin(despertar_por="monitor", instance_id=instance_id)
    signals: dict = {}
    try:
        signals = gather_signals(config, instance_id=instance_id)
        if _live_enabled(config):
            report = await _live_report(
                signals,
                department=department,
                instance_id=instance_id,
                config=config,
                skill_name=skill_name,
            )
        else:
            report = _offline_report(signals, department)

        resultado = (
            f"{len(report.get('risks') or [])} risk(s), "
            f"{len(report.get('improvements') or [])} improvement(s), "
            f"{len(report.get('delegations') or [])} delegation(s)"
        )
        ep.finish(
            decision=f"monitor: {'live' if report.get('live') else 'offline'}",
            accion=f"monitor:{department}",
            resultado=resultado,
            costo_usd=float(report.get("cost_usd") or 0.0),
        )
        return report
    except Exception as exc:  # never raise out of a monitoring pass
        logger.error("monitor_pass failed for dept:{}: {}", department, exc)
        report = _offline_report(signals or {}, department)
        report["error"] = f"{type(exc).__name__}: {exc}"
        ep.finish(
            decision="monitor: error",
            accion=f"monitor:{department} error",
            resultado=str(exc),
        )
        return report
    finally:
        with contextlib.suppress(Exception):
            await ep.append()


# ── reusable wiring: make ANY brain a monitor ─────────────────────────────────────


def ensure_monitor_skill(config: "Optional[Config]" = None):
    """Ensure the ``monitor`` SKILL.md exists in the store; create it if missing.

    Idempotent: an already-installed, non-empty skill is returned unchanged. Returns
    the stored :class:`~lhassa.skills.skillmd.Skill`.
    """
    from lhassa.skills.skillmd import Skill
    from lhassa.skills.store import SkillStore

    store = SkillStore(config)
    existing = store.get(MONITOR_SKILL_NAME)
    if (
        existing is not None
        and not existing.error
        and (existing.instructions or "").strip()
    ):
        return existing
    skill = Skill(
        name=MONITOR_SKILL_NAME,
        description=MONITOR_DESCRIPTION,
        instructions=_MONITOR_INSTRUCTIONS,
        metadata={
            "domain": "supervision",
            "tags": ["monitoring", "supervision", "continuous-improvement"],
        },
    )
    store.save(skill)
    return store.get(MONITOR_SKILL_NAME) or skill


def make_brain_monitor(
    brain_id: str,
    *,
    config: "Optional[Config]" = None,
    role: str = MONITOR_ROLE,
    description: str = MONITOR_DESCRIPTION,
    skill_name: str = MONITOR_SKILL_NAME,
) -> dict:
    """Make ``brain_id`` the environment monitor — REUSABLE for any brain.

    Ensures the monitor skill exists, assigns it to the brain (merging, never
    dropping existing skills), and updates the brain's topology node's role +
    description note to reflect "environment monitor & continuous-improvement
    supervisor — everyone reports to it". The description is stashed in the node's
    opaque ``limits`` mapping (no schema change) and is fully reversible.

    If ``brain_id`` is not in the topology this is a NO-OP with a clear log — it
    returns ``{"ok": False, "reason": ...}`` and touches nothing. Never raises for a
    missing brain; other errors propagate (a real misconfiguration).
    """
    cfg = _resolve_config(config)
    ensure_monitor_skill(cfg)

    from lhassa.org import management
    from lhassa.org.topology import load_topology

    top = load_topology(cfg)
    if not top.has(brain_id):
        logger.info(
            "monitor: brain {!r} not in topology — skipping monitor wiring", brain_id
        )
        return {"ok": False, "reason": f"no brain {brain_id!r} in topology", "brain": brain_id}

    # Assign the skill (merges into the brain's current skill set).
    from lhassa.skills.store import assign_to_brain

    assign_to_brain(skill_name, brain_id, cfg)

    # Update role + stash the human description in the opaque limits mapping.
    node = load_topology(cfg).node(brain_id)
    limits = dict(node.limits or {})
    limits["description"] = description
    management.update_brain(brain_id, role=role, limits=limits, config=cfg)

    return {
        "ok": True,
        "brain": brain_id,
        "skill": skill_name,
        "role": role,
        "description": description,
    }
