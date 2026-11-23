# SPDX-License-Identifier: AGPL-3.0-or-later
"""``personal.agenda.days`` / ``commitments`` and the dated event-source seam."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lhassa.core import db
from lhassa.personal import agenda, sources


class Cfg:
    instance_id = "inst_days"


async def test_days_buckets_dated_events_by_local_day(monkeypatch):
    now = datetime.now(timezone.utc)
    today = now.astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    seen = {}

    async def fake(instance_id, *, since, until, config=None):
        seen.update(since=since, until=until)
        return [
            {"start": today + timedelta(days=1), "summary": "tomorrow"},
            {"start": today, "summary": "today"},
            {"start": today + timedelta(days=5), "summary": "out of range"},
            {"summary": "no start"},
        ]

    monkeypatch.setattr(sources, "AGENDA_DATED_SOURCES", type(sources.AGENDA_DATED_SOURCES)("t"))
    sources.register_dated_event_source("fake", fake)
    out = await agenda.days(Cfg, count=2, now=now)
    assert [d["date"] for d in out] == [today.date(), (today + timedelta(days=1)).date()]
    assert [e["summary"] for e in out[0]["events"]] == ["today"]
    assert [e["summary"] for e in out[1]["events"]] == ["tomorrow"]
    assert seen["until"] - seen["since"] == timedelta(days=2)


async def test_days_survive_a_failing_source(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("feed down")

    monkeypatch.setattr(sources, "AGENDA_DATED_SOURCES", type(sources.AGENDA_DATED_SOURCES)("t"))
    sources.register_dated_event_source("boom", boom)
    out = await agenda.days(Cfg)
    assert len(out) == 2 and all(d["events"] == [] for d in out)


async def test_commitments_are_reminders_and_goals_only():
    await db.init_db()
    soon = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    await db.add_task("inst_days", "send_reminder", "x", {"text": "Call the bank"}, due_at=later)
    await db.add_task("inst_days", "goal", "Ship the paper", {}, due_at=soon, recurrence="604800")
    await db.add_task("inst_days", "temple_run", "", {}, due_at=soon)
    got = await agenda.commitments(Cfg)
    assert [(c.kind, c.text, c.every_seconds) for c in got] == [
        ("goal", "Ship the paper", 604800),
        ("reminder", "Call the bank", None),
    ]
