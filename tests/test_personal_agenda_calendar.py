"""Personal agenda rendering of calendar names — additive to the existing
build()/render() behavior. Offline: lhassa.calendar.feed.upcoming is
monkeypatched to fake events, no network or real ICS involved.
"""

from datetime import datetime, timezone

import pytest

from lhassa.personal import agenda


def _event(summary: str, hour: int, calendar: str | None = None) -> dict:
    ev = {
        "summary": summary,
        "location": "",
        "all_day": False,
        "start": datetime(2026, 7, 8, hour, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 7, 8, hour + 1, 0, tzinfo=timezone.utc),
    }
    if calendar is not None:
        ev["calendar"] = calendar
    return ev


@pytest.mark.asyncio
async def test_agenda_shows_calendar_name_with_multiple_calendars(monkeypatch):
    from lhassa.calendar import feed as _feed

    async def _fake_upcoming(instance_id, *, hours=36, since=None, config=None):
        return [
            _event("Algorithms 201", 15, "Work"),
            _event("Team sync", 19, "Personal"),
        ]

    monkeypatch.setattr(_feed, "upcoming", _fake_upcoming)

    from lhassa.core import db as _db
    from lhassa.memory import checkins
    from lhassa.security import approval

    monkeypatch.setattr(_db, "init_db", _async_return(None))
    monkeypatch.setattr(_db, "list_tasks", _async_return([]))
    monkeypatch.setattr(approval, "pending", _async_return([]))
    monkeypatch.setattr(checkins, "latest", _async_return(None))

    class _Cfg:
        instance_id = "inst_personal"

    summary = await agenda.build(_Cfg())
    assert any("Work" in e for e in summary.events)
    assert any("Personal" in e for e in summary.events)


@pytest.mark.asyncio
async def test_agenda_omits_calendar_name_with_a_single_calendar(monkeypatch):
    from lhassa.calendar import feed as _feed

    async def _fake_upcoming(instance_id, *, hours=36, since=None, config=None):
        return [_event("Algorithms 201", 15, "Work")]

    monkeypatch.setattr(_feed, "upcoming", _fake_upcoming)

    from lhassa.core import db as _db
    from lhassa.memory import checkins
    from lhassa.security import approval

    monkeypatch.setattr(_db, "init_db", _async_return(None))
    monkeypatch.setattr(_db, "list_tasks", _async_return([]))
    monkeypatch.setattr(approval, "pending", _async_return([]))
    monkeypatch.setattr(checkins, "latest", _async_return(None))

    class _Cfg:
        instance_id = "inst_personal"

    summary = await agenda.build(_Cfg())
    assert len(summary.events) == 1
    assert summary.events[0].endswith("Algorithms 201")
    assert "Work" not in summary.events[0]


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner
