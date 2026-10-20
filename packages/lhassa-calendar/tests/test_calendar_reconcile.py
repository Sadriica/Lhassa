"""Tests for the master-calendar reconcile diff engine (lhassa/calendar/reconcile.py).

Offline, no network: :func:`lhassa.calendar.feed.upcoming` is monkeypatched to
return fake, already-tagged events (no real feed fetch), and the calendar store
points at a tmp_path file.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from lhassa.calendar import reconcile, store


def _cfg(tmp_path, **over):
    base = dict(
        calendars_file=str(tmp_path / "calendars.json"),
        calendar_ics_url="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ev(summary, start, *, calendar, end=None, location=""):
    return {
        "summary": summary,
        "start": start,
        "end": end or (start + timedelta(hours=1)),
        "location": location,
        "all_day": False,
        "calendar": calendar,
    }


_NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


# ── find_unsynced ──────────────────────────────────────────────────────────────


def test_no_primary_configured_returns_empty(tmp_path):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    # no primary set

    import asyncio

    out = asyncio.run(reconcile.find_unsynced("inst", config=cfg))
    assert out == []


def test_missing_event_is_a_candidate(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.add_calendar("Personal", "https://example.com/personal.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    events = [
        _ev("Algorithms 201", _NOW + timedelta(hours=3), calendar="Work"),
        _ev("Team meeting", _NOW + timedelta(hours=5), calendar="Personal"),
    ]

    async def fake_upcoming(instance_id, *, hours=168, config=None):
        return events

    import lhassa.calendar.feed as feed_mod

    monkeypatch.setattr(feed_mod, "upcoming", fake_upcoming)

    import asyncio

    out = asyncio.run(reconcile.find_unsynced("inst", config=cfg))
    assert len(out) == 1
    assert out[0]["summary"] == "Team meeting"
    assert out[0]["source_calendar"] == "Personal"


def test_matching_event_on_primary_is_not_a_candidate(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.add_calendar("Personal", "https://example.com/personal.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    start = _NOW + timedelta(hours=5)
    events = [
        _ev("Team meeting", start, calendar="Work"),
        _ev("Team meeting", start, calendar="Personal"),
    ]

    async def fake_upcoming(instance_id, *, hours=168, config=None):
        return events

    import lhassa.calendar.feed as feed_mod

    monkeypatch.setattr(feed_mod, "upcoming", fake_upcoming)

    import asyncio

    out = asyncio.run(reconcile.find_unsynced("inst", config=cfg))
    assert out == []


def test_matching_is_case_and_whitespace_insensitive(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.add_calendar("Personal", "https://example.com/personal.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    start = _NOW + timedelta(hours=5)
    events = [
        _ev("  team   meeting ", start, calendar="Work"),
        _ev("Team meeting", start, calendar="Personal"),
    ]

    async def fake_upcoming(instance_id, *, hours=168, config=None):
        return events

    import lhassa.calendar.feed as feed_mod

    monkeypatch.setattr(feed_mod, "upcoming", fake_upcoming)

    import asyncio

    out = asyncio.run(reconcile.find_unsynced("inst", config=cfg))
    assert out == []


def test_matching_within_tolerance_minutes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.add_calendar("Personal", "https://example.com/personal.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    primary_start = _NOW + timedelta(hours=5)
    secondary_start = primary_start + timedelta(minutes=3)  # within tolerance
    events = [
        _ev("Team meeting", primary_start, calendar="Work"),
        _ev("Team meeting", secondary_start, calendar="Personal"),
    ]

    async def fake_upcoming(instance_id, *, hours=168, config=None):
        return events

    import lhassa.calendar.feed as feed_mod

    monkeypatch.setattr(feed_mod, "upcoming", fake_upcoming)

    import asyncio

    out = asyncio.run(reconcile.find_unsynced("inst", config=cfg))
    assert out == []


def test_outside_tolerance_is_unsynced(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.add_calendar("Personal", "https://example.com/personal.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    primary_start = _NOW + timedelta(hours=5)
    secondary_start = primary_start + timedelta(minutes=30)  # far outside tolerance
    events = [
        _ev("Team meeting", primary_start, calendar="Work"),
        _ev("Team meeting", secondary_start, calendar="Personal"),
    ]

    async def fake_upcoming(instance_id, *, hours=168, config=None):
        return events

    import lhassa.calendar.feed as feed_mod

    monkeypatch.setattr(feed_mod, "upcoming", fake_upcoming)

    import asyncio

    out = asyncio.run(reconcile.find_unsynced("inst", config=cfg))
    assert len(out) == 1


def test_no_feeds_at_all_returns_empty(tmp_path):
    cfg = _cfg(tmp_path)
    # A primary is set on... nothing (no calendars stored at all).
    import asyncio

    out = asyncio.run(reconcile.find_unsynced("inst", config=cfg))
    assert out == []


def test_upcoming_failure_degrades_to_empty(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    async def boom(instance_id, *, hours=168, config=None):
        raise RuntimeError("network is down")

    import lhassa.calendar.feed as feed_mod

    monkeypatch.setattr(feed_mod, "upcoming", boom)

    import asyncio

    out = asyncio.run(reconcile.find_unsynced("inst", config=cfg))
    assert out == []


# ── to_ics ──────────────────────────────────────────────────────────────────────


def test_to_ics_produces_a_valid_vevent():
    candidate = {
        "summary": "Team meeting",
        "start": _NOW + timedelta(hours=3),
        "end": _NOW + timedelta(hours=4),
        "location": "Zoom",
        "source_calendar": "Personal",
    }
    content = reconcile.to_ics(candidate)
    assert "BEGIN:VEVENT" in content
    assert "SUMMARY:Team meeting" in content
    assert "UID:reconcile-" in content


def test_to_ics_stable_uid_same_inputs_same_uid():
    candidate = {
        "summary": "Team meeting",
        "start": _NOW + timedelta(hours=3),
        "end": None,
        "location": "",
        "source_calendar": "Personal",
    }
    c1 = reconcile.to_ics(dict(candidate))
    c2 = reconcile.to_ics(dict(candidate))

    def _uid(content):
        for line in content.splitlines():
            if line.startswith("UID:"):
                return line
        return None

    assert _uid(c1) == _uid(c2)
    assert _uid(c1) is not None


def test_to_ics_missing_start_degrades_to_empty():
    assert reconcile.to_ics({"summary": "x"}) == ""
