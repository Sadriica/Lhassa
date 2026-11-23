"""Tests for lhassa.calendar.derive; the calendar-derived world model (leaf).

Offline, no network: :func:`lhassa.calendar.feed.upcoming` is monkeypatched to
return fake, already-tagged events (no real feed fetch). Covers the heuristics
documented in the module: recurring commitment/course grouping (with the room
hint upgrade), the meal/break denylist, the ≥3-occurrence floor, and the
rhythm signal (present with enough skew, absent otherwise).
"""

import asyncio
from datetime import datetime, timedelta, timezone

import lhassa.calendar.feed as feed_mod
from lhassa.calendar import derive

_BASE = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)  # a Tuesday, local=UTC here


def _ev(summary, start, *, location="", all_day=False):
    return {
        "summary": summary,
        "start": start,
        "end": start + timedelta(hours=1),
        "location": location,
        "all_day": all_day,
        "calendar": "default",
    }


def _weekly(summary, n=4, *, start=_BASE, location=""):
    return [_ev(summary, start + timedelta(weeks=i), location=location) for i in range(n)]


def _patch_upcoming(monkeypatch, events):
    async def fake_upcoming(instance_id, *, hours=36, since=None, config=None):
        return events

    monkeypatch.setattr(feed_mod, "upcoming", fake_upcoming)


def test_no_events_yields_no_facts(monkeypatch):
    _patch_upcoming(monkeypatch, [])
    out = asyncio.run(derive.derive_world("inst", days=45))
    assert out == []


def test_recurring_weekly_course_becomes_a_fact(monkeypatch):
    events = _weekly("Algorithms 201", n=4, location="Room 204")
    _patch_upcoming(monkeypatch, events)

    out = asyncio.run(derive.derive_world("inst", days=45))
    course_facts = [f for f in out if "course" in f["tags"]]
    assert len(course_facts) == 1
    fact = course_facts[0]
    assert "Algorithms 201" in fact["contenido"]
    assert "course" in fact["contenido"] or "class" in fact["contenido"]
    assert fact["procedencia"] == "calendar-derived"
    assert "calendar" in fact["tags"]


def test_recurring_meeting_without_room_is_a_commitment_not_a_course(monkeypatch):
    events = _weekly("Team sync", n=4, location="")
    _patch_upcoming(monkeypatch, events)

    out = asyncio.run(derive.derive_world("inst", days=45))
    matches = [f for f in out if "Team sync" in f["contenido"]]
    assert len(matches) == 1
    assert matches[0]["tags"] == ["calendar", "commitment"]
    assert "course" not in matches[0]["contenido"]


def test_recurring_lunch_is_filtered_out(monkeypatch):
    events = _weekly("Almuerzo equipo", n=5, location="Sala 3")
    _patch_upcoming(monkeypatch, events)

    out = asyncio.run(derive.derive_world("inst", days=45))
    assert all("almuerzo" not in f["contenido"].lower() for f in out)


def test_one_off_event_yields_no_commitment_fact(monkeypatch):
    events = [_ev("Dentist appointment", _BASE, location="")]
    _patch_upcoming(monkeypatch, events)

    out = asyncio.run(derive.derive_world("inst", days=45))
    assert not any("Dentist" in f["contenido"] for f in out)


def test_two_occurrences_is_not_enough(monkeypatch):
    events = _weekly("Física II", n=2, location="Bloque 4")
    _patch_upcoming(monkeypatch, events)

    out = asyncio.run(derive.derive_world("inst", days=45))
    assert not any("Física II" in f["contenido"] for f in out)


def test_irregular_gaps_are_not_a_cadence(monkeypatch):
    events = [
        _ev("Ad hoc review", _BASE),
        _ev("Ad hoc review", _BASE + timedelta(days=1)),
        _ev("Ad hoc review", _BASE + timedelta(days=40)),
    ]
    _patch_upcoming(monkeypatch, events)

    out = asyncio.run(derive.derive_world("inst", days=60))
    assert not any("Ad hoc review" in f["contenido"] for f in out)


def test_rhythm_fact_present_with_enough_skew(monkeypatch):
    # 6 events all Tuesday morning-ish, all in the same weekday+time bucket.
    events = [_ev(f"Thing {i}", _BASE + timedelta(weeks=i)) for i in range(6)]
    _patch_upcoming(monkeypatch, events)

    out = asyncio.run(derive.derive_world("inst", days=60))
    rhythm = [f for f in out if "rhythm" in f["tags"]]
    assert len(rhythm) == 1
    assert "busiest" in rhythm[0]["contenido"]


def test_rhythm_fact_absent_without_enough_signal(monkeypatch):
    events = [_ev("Solo event", _BASE)]
    _patch_upcoming(monkeypatch, events)

    out = asyncio.run(derive.derive_world("inst", days=45))
    assert not any("rhythm" in f["tags"] for f in out)


def test_all_day_events_are_excluded_from_commitments_and_rhythm(monkeypatch):
    events = [
        _ev("Public holiday", _BASE + timedelta(weeks=i), all_day=True) for i in range(4)
    ]
    _patch_upcoming(monkeypatch, events)

    out = asyncio.run(derive.derive_world("inst", days=45))
    assert out == []


def test_never_raises_on_feed_error(monkeypatch):
    async def boom(instance_id, *, hours=36, since=None, config=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(feed_mod, "upcoming", boom)
    out = asyncio.run(derive.derive_world("inst", days=45))
    assert out == []
