# SPDX-License-Identifier: AGPL-3.0-or-later
"""Org-chart-facing commands; /org /fleet /temple.

Moved verbatim out of the former ``lhassa/notify/commands.py``.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from ._registry import register_command
from ._shared import _config, _oops

__all__ = ["_match_temple_action"]


@register_command("org")
async def _cmd_org(args: str, *, config=None) -> str:
    """``/org``: the org chart as a compact indented tree, LLM-free.

    Renders :func:`lhassa.org.topology.load_topology`: each brain's id, role and
    department (when it has one), indented by depth. The permanent administrator
    (the root) is marked ``★``; a coordinating brain ``○``; a leaf that actually
    executes ``●``. Never constructs a provider; reads the topology file/derived
    default only.
    """
    try:
        cfg = _config(config)
        from lhassa.org.topology import load_topology

        topology = load_topology(cfg)
        nodes = topology.nodes()
        if not nodes:
            return _oops("read the org chart")

        lines = ["Org chart:"]

        def render(node_id: str, depth: int) -> None:
            node = topology.node(node_id)
            if topology.is_admin(node_id):
                marker = "★"
            elif topology.is_leaf(node_id):
                marker = "●"
            else:
                marker = "○"
            dept = f" [{node.department}]" if node.department else ""
            lines.append("  " * depth + f"{marker} {node.id} ({node.role}){dept}")
            for child in topology.children_of(node_id):
                render(child.id, depth + 1)

        render(topology.root().id, 0)
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /org failed: {exc}")
        return _oops("read the org chart")


@register_command("fleet")
async def _cmd_fleet(args: str, *, config=None) -> str:
    """``/fleet``: known machines, last check-in and desired state, LLM-free.

    Reuses :func:`lhassa.core.fleet.list_fleet`, the exact same read
    ``/api/fleet`` serves, rather than re-deriving the fleet from episodes.
    """
    try:
        from lhassa.core import fleet

        rows = await fleet.list_fleet()
        if not rows:
            return "No machines known yet."
        lines = [f"Fleet ({len(rows)}):"]
        for r in rows[:10]:
            mid = r.get("machine_id") or "?"
            last = r.get("last_checkin") or "never"
            ds = r.get("desired_state") or {}
            ds_str = ", ".join(f"{k}={v}" for k, v in ds.items()) or "—"
            lines.append(f"- {mid}: last {last} · {ds_str}")
        if len(rows) > 10:
            lines.append(f"…+{len(rows) - 10} more")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /fleet failed: {exc}")
        return _oops("read the fleet")


# Free-form instruction -> a known temple action, LLM-free (a keyword match, not
# NLP). Anything that doesn't match falls through to org_run(department="temple")
#; the judgment door for internal work that isn't one of these fixed actions.
_TEMPLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("distill", "distill_memory"),
    ("repetition", "detect_repetition"),
    ("repeat", "detect_repetition"),
    ("review tool", "review_tools"),
    ("curate", "curate_skills"),
    ("skill", "curate_skills"),
    # Checked BEFORE the bare "calendar"/"sync" keywords below so a phrase
    # like "learn calendar" or "derive world" routes to the world-model
    # derivation, not the master-calendar reconcile.
    ("derive", "derive_world_facts"),
    ("world model", "derive_world_facts"),
    ("world", "derive_world_facts"),
    ("learn calendar", "derive_world_facts"),
    ("calendar", "calendar_sync"),
    # Checked BEFORE the bare "sync" keyword so "sync my notes"/"sync knowledge"
    # routes to document perception, not the master-calendar reconcile.
    ("sync my notes", "ingest_knowledge"),
    ("sync knowledge", "ingest_knowledge"),
    ("index my notes", "ingest_knowledge"),
    ("knowledge", "ingest_knowledge"),
    ("sync", "calendar_sync"),
)


def _match_temple_action(text: str) -> Optional[str]:
    """The known temple action ``text`` maps to, or ``None``: LLM-free."""
    lowered = text.lower()
    for keyword, action in _TEMPLE_KEYWORDS:
        if keyword in lowered:
            return action
    return None


def _cadence_label(seconds: int) -> str:
    """A human cadence from a recurrence interval in seconds ($0)."""
    if seconds <= 0:
        return "once"
    day = 86400
    if seconds % (7 * day) == 0:
        n = seconds // (7 * day)
        return "weekly" if n == 1 else f"every {n} weeks"
    if seconds % day == 0:
        n = seconds // day
        return "daily" if n == 1 else f"every {n} days"
    if seconds % 3600 == 0:
        n = seconds // 3600
        return "hourly" if n == 1 else f"every {n}h"
    return f"every {seconds}s"


def _short_local_due(raw: str) -> str:
    """An ISO due timestamp → a short local 'Wed 08:00', or '' if unparseable ($0)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%a %H:%M")
    except (TypeError, ValueError):
        return ""


async def _temple_schedule(config) -> str:
    """The temple's internal clock; its recurring work, each job's cadence and
    the monk that owns it. Deterministic, $0, never an LLM call.

    Reads the pending recurring ``temple_run`` tasks (the internal cronjobs the
    ``maintenance`` seed mints) and enriches each with its cadence and owning
    worker from :data:`lhassa.org.temple.ACTION_OWNERS`. Owner-facing recurring
    work (digest, reminders) is NOT the temple's and is deliberately excluded.
    """
    try:
        from lhassa.core import db as _db
        from lhassa.org.temple import ACTION_OWNERS, TEMPLE_DEPARTMENT

        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        await _db.init_db()
        rows = await _db.list_tasks(instance_id, estado="pendiente", limit=200)

        jobs: list[tuple[str, str, str, str]] = []
        for row in rows:
            if row.get("accion") != "temple_run":
                continue
            rec = row.get("recurrence")
            if not rec:
                continue
            params = row.get("params") or {}
            action = params.get("action") if isinstance(params, dict) else None
            if not action:
                continue
            try:
                cadence = _cadence_label(int(rec))
            except (TypeError, ValueError):
                cadence = "recurring"
            worker = ACTION_OWNERS.get(action, TEMPLE_DEPARTMENT)
            due = _short_local_due(str(row.get("due_at") or ""))
            jobs.append((action, cadence, worker, due))

        if not jobs:
            return (
                "The temple's clock is empty; no recurring internal work is "
                "scheduled. Run `lhassa seed apply maintenance` (or /temple <task>) "
                "to give it a rhythm."
            )
        jobs.sort(key=lambda j: (j[2], j[0]))
        lines = ["🛕 The temple's clock; recurring internal work:"]
        for action, cadence, worker, due in jobs:
            tail = f" · next {due}" if due else ""
            lines.append(f"• {action}; {cadence} · {worker}{tail}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /temple schedule failed: {exc}")
        return _oops("read the temple's schedule")


@register_command("temple")
async def _cmd_temple(args: str, *, config=None) -> str:
    """``/temple <instruction>``: hand internal work to Lhassa's own inner
    department (temple), the down-execute-up loop (see :mod:`lhassa.org.temple`).

    ``/temple schedule`` (or ``jobs``) shows the temple's own clock; its
    recurring internal work with each job's cadence and owning monk, $0.

    LLM-free where the instruction keyword-matches a known deterministic
    action (distill/repetition/review tools/curate skills/calendar sync/
    derive world); runs it immediately through
    :func:`lhassa.org.temple.delegate_internal` and returns the result.
    Otherwise mints an ``org_run`` task with ``department="temple"``: the
    judgment door, resolved on the next tick.
    """
    text = (args or "").strip()
    if not text:
        return "Usage: /temple <instruction> (e.g. /temple distill recent episodes) · /temple schedule"

    if text.lower() in ("schedule", "jobs", "clock", "reloj", "agenda"):
        return await _temple_schedule(config)

    cfg = _config(config)
    action = _match_temple_action(text)
    if action:
        try:
            from lhassa.org.temple import delegate_internal

            record = await delegate_internal(action, {}, config=cfg, instruction=text)
        except Exception as exc:
            logger.warning(f"commands: /temple failed running {action}: {exc}")
            return _oops("run that through temple")
        result = str(record.get("result") or "")[:300]
        return f"temple ran {action}: {result}"

    # No fixed-action match; the judgment door: org_run(department="temple").
    try:
        from lhassa.core import db as _db
        from lhassa.core.dispatch import mint_task
        from lhassa.security.trust import Origin, TrustLevel

        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        await _db.init_db()
        origin = Origin(trust=TrustLevel.HUMANO, actor_id="telegram")
        task_id, estado = await mint_task(
            instance_id,
            "org_run",
            objetivo=text[:200],
            params={"goal": text, "department": "temple"},
            origin=origin,
        )
    except Exception as exc:
        logger.warning(f"commands: /temple delegate failed: {exc}")
        return _oops("hand that to temple")

    if not task_id:
        return f"Could not hand that to temple: {estado}"
    return f"Handed to temple: {text} [task {task_id}]"
