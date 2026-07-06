"""Personal daily check-ins stored in the grande as typed facts.

This is the first secretary-shaped memory: short owner-authored notes from the
morning/evening digest. It intentionally rides on ``facts`` for now so it is
append-only, instance-scoped, searchable, and available to memory load without a
new schema migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from lhassa.config import Config
from lhassa.core import db
from lhassa.memory import facts

TAG_PERSONAL = "personal_checkin"
TAG_DAILY = "daily_checkin"


@dataclass
class DailyCheckin:
    id: str
    content: str
    slot: str
    created_at: str


def _instance(instance_id: str | None) -> str:
    return instance_id or Config.instance_id


def _slot(slot: str | None = None) -> str:
    raw = (slot or "").strip().lower()
    if raw in {"morning", "evening"}:
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


async def record(
    instance_id: str | None,
    text: str,
    *,
    slot: str | None = None,
    source: str = "telegram-checkin",
) -> DailyCheckin:
    """Append one owner-authored check-in note."""
    body = (text or "").strip()
    if not body:
        raise ValueError("check-in text is empty")
    await db.init_db()
    picked_slot = _slot(slot)
    fact = await facts.add_fact(
        _instance(instance_id),
        "dato",
        body[:1000],
        tags=[TAG_PERSONAL, TAG_DAILY, f"slot:{picked_slot}"],
        procedencia=source,
        confianza=0.95,
    )
    return DailyCheckin(
        id=fact.id,
        content=fact.contenido,
        slot=picked_slot,
        created_at=fact.creado_en,
    )


async def latest(
    instance_id: str | None,
    *,
    within_hours: float | None = 36.0,
) -> DailyCheckin | None:
    """Return the newest personal check-in, optionally bounded by age."""
    rows = await facts.load_by_tags(_instance(instance_id), [TAG_DAILY])
    rows = [f for f in rows if TAG_DAILY in set(f.tags or [])]
    if not rows:
        return None
    rows.sort(key=lambda f: (f.creado_en or "", f.id), reverse=True)
    newest = rows[0]
    if within_hours is not None:
        dt = _parse_dt(newest.creado_en)
        if dt is not None:
            age = datetime.now(timezone.utc) - dt
            if age > timedelta(hours=float(within_hours)):
                return None
    slot = "check-in"
    for tag in newest.tags:
        if tag.startswith("slot:"):
            slot = tag.split(":", 1)[1] or slot
            break
    return DailyCheckin(
        id=newest.id,
        content=newest.contenido,
        slot=slot,
        created_at=newest.creado_en,
    )
