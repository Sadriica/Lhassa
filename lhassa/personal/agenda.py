# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic personal agenda summaries.

This is the reusable secretary layer. It reads only local state: pending tasks,
approvals, the latest owner check-in, and the cost ledger. No model call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class AgendaTask:
    id: str
    action: str
    due_at: str = ""


@dataclass
class PersonalAgenda:
    instance_id: str
    slot: str
    pending_tasks: list[AgendaTask] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    latest_checkin: str = ""
    spend_today_usd: Optional[float] = None
    # Events from the registered agenda sources (lhassa.personal.sources; the
    # calendar pack's ICS feeds) as "HH:MM Summary" strings in local time,
    # already windowed + sorted. Empty when no source is installed/configured.
    events: list[str] = field(default_factory=list)

    @property
    def priorities(self) -> list[str]:
        out: list[str] = []
        for ev in self.events[:3]:
            out.append(f"📅 {ev}")
        for title in self.approvals[:2]:
            out.append(f"Resolve approval: {title}")
        for task in self.pending_tasks[:3]:
            label = task.action
            if task.due_at:
                label += f" ({task.due_at})"
            out.append(label)
        return out[:5]


def _instance(config) -> str:
    return getattr(config, "instance_id", None) or "inst_personal"


def _slot(slot: str | None = None) -> str:
    raw = (slot or "").strip().lower()
    if raw in {"today", "tomorrow", "morning", "evening"}:
        return raw
    hour = datetime.now().hour
    return "morning" if hour < 14 else "evening"


def _parse_dt(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _short_due(raw: str) -> str:
    dt = _parse_dt(raw)
    if dt is None:
        return ""
    return dt.astimezone().strftime("%a %H:%M")


async def _today_spend(config) -> Optional[float]:
    """Today's cost from the gateway's own spend signal (interface layer; the
    personal package must never import a shell like notify)."""
    try:
        from lhassa.llm.gateway import spend_summary

        summary = await spend_summary(config)
        return float(summary.get("cost_usd") or 0.0)
    except Exception:
        return None


async def build(config, *, slot: str | None = None, horizon_hours: int = 36) -> PersonalAgenda:
    from lhassa.core import db
    from lhassa.memory import checkins
    from lhassa.security import approval

    await db.init_db()
    instance_id = _instance(config)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_hours)

    tasks = []
    for row in await db.list_tasks(instance_id, estado="pendiente", limit=200):
        due_raw = str(row.get("due_at") or "")
        due_dt = _parse_dt(due_raw) if due_raw else None
        if due_dt is not None and due_dt > horizon:
            continue
        tasks.append(
            AgendaTask(
                id=str(row.get("id") or ""),
                action=str(row.get("accion") or row.get("objetivo") or "task"),
                due_at=_short_due(due_raw),
            )
        )

    approval_titles = []
    for row in await approval.pending(instance_id):
        with_title = approval.describe(row).get("title") or row.get("tipo") or "approval"
        approval_titles.append(str(with_title))

    latest = await checkins.latest(instance_id, within_hours=48.0)

    # Events from every registered agenda source (the calendar pack's ICS
    # feeds, when installed); additive, $0, never breaks the agenda if a
    # source is absent or unreachable.
    from lhassa.personal.sources import collect_events

    try:
        events = await collect_events(instance_id, hours=horizon_hours, config=config)
    except Exception:
        events = []

    return PersonalAgenda(
        instance_id=instance_id,
        slot=_slot(slot),
        pending_tasks=tasks,
        approvals=approval_titles,
        latest_checkin=latest.content if latest else "",
        spend_today_usd=await _today_spend(config),
        events=events,
    )


def render(agenda: PersonalAgenda, *, language: str = "en") -> str:
    spanish = (language or "").lower().startswith("es")
    priorities = agenda.priorities
    lines: list[str] = []
    if spanish:
        title = "Para mañana veo" if agenda.slot in {"tomorrow", "evening"} else "Para hoy veo"
        lines.append(f"{title} {len(priorities)} prioridad(es):")
        if priorities:
            lines.extend(f"{i}. {p}" for i, p in enumerate(priorities, 1))
        else:
            lines.append("No veo nada urgente en la cola local.")
        if agenda.latest_checkin:
            lines.append(f"Último check-in: {agenda.latest_checkin[:160]}")
        if agenda.spend_today_usd is not None:
            lines.append(f"Gasto de hoy: ${agenda.spend_today_usd:.4f}")
        lines.append("Puedes responder /goal si quieres que lo vigile.")
        return "\n".join(lines)

    title = "For tomorrow I see" if agenda.slot in {"tomorrow", "evening"} else "For today I see"
    lines.append(f"{title} {len(priorities)} priority item(s):")
    if priorities:
        lines.extend(f"{i}. {p}" for i, p in enumerate(priorities, 1))
    else:
        lines.append("I do not see anything urgent in the local queue.")
    if agenda.latest_checkin:
        lines.append(f"Last check-in: {agenda.latest_checkin[:160]}")
    if agenda.spend_today_usd is not None:
        lines.append(f"Spend today: ${agenda.spend_today_usd:.4f}")
    lines.append("Reply with /goal if you want me to keep watch.")
    return "\n".join(lines)



# ── days and commitments: the agenda laid out for a screen ───────────────────

#: Task actions that are the owner's own commitments (things they asked Lhassa
#: to remind them of or keep watch over); everything else in the queue is
#: Lhassa's own upkeep.
COMMITMENT_ACTIONS = {"send_reminder": "reminder", "goal": "goal"}


@dataclass
class Commitment:
    kind: str  # "reminder" | "goal"
    text: str
    due: Optional[datetime]
    every_seconds: Optional[int] = None


def _local_midnight(now: datetime) -> datetime:
    local = now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


async def days(config, *, count: int = 2, now: Optional[datetime] = None) -> list[dict]:
    """``count`` local days from today: ``[{"date", "events"}]``, events sorted.

    Today starts at local midnight, so events already past still show (a
    surface may dim them). Events come from the dated agenda sources (the
    calendar pack); with none installed every day is empty. Never raises.
    """
    from lhassa.personal.sources import collect_dated_events

    now = now or datetime.now(timezone.utc)
    start = _local_midnight(now)
    bounds = [start + timedelta(days=i) for i in range(count + 1)]
    out = [{"date": bounds[i].date(), "events": []} for i in range(count)]
    try:
        events = await collect_dated_events(
            _instance(config), since=bounds[0], until=bounds[-1], config=config
        )
    except Exception:
        events = []
    for ev in events:
        try:
            local_day = ev["start"].astimezone().date()
        except Exception:
            continue
        for day in out:
            if day["date"] == local_day:
                day["events"].append(ev)
                break
    return out


def _task_text(row: dict) -> str:
    params = row.get("params") or {}
    if isinstance(params, dict) and params.get("text"):
        return str(params["text"])
    return str(row.get("objetivo") or row.get("accion") or "")


async def commitments(config, *, limit: int = 6) -> list[Commitment]:
    """The owner's pending reminders and goals, soonest first. Never raises."""
    try:
        from lhassa.core import db

        await db.init_db()
        rows = await db.list_tasks(_instance(config), estado="pendiente", limit=500)
    except Exception:
        return []
    out: list[Commitment] = []
    for row in rows:
        kind = COMMITMENT_ACTIONS.get(str(row.get("accion") or ""))
        if kind is None:
            continue
        due = _parse_dt(str(row.get("due_at") or "")) if row.get("due_at") else None
        try:
            every = int(row["recurrence"]) if row.get("recurrence") else None
        except (TypeError, ValueError):
            every = None
        out.append(Commitment(kind=kind, text=_task_text(row).strip(), due=due, every_seconds=every))
    far = datetime.max.replace(tzinfo=timezone.utc)
    out.sort(key=lambda c: c.due or far)
    return out[:limit]
