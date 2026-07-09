"""Tests for the deterministic, $0 calendar-query path (lhassa/calendar/query.py).

No network: :func:`lhassa.calendar.feed.upcoming` is monkeypatched with fake
in-memory events (never a real ICS fetch). All assertions are offline.
"""

from datetime import datetime, timedelta, timezone

import pytest

from lhassa.calendar import query


# A fixed "now": Wednesday 2026-07-08 10:00 UTC. (Today's real date happens to
# also be 2026-07-08, but the tests never rely on that — ``now`` is always
# passed explicitly to parse_window, or monkeypatched for answer().)
_NOW = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)  # a Wednesday


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# ── parse_window ─────────────────────────────────────────────────────────────


def test_parse_window_hoy_today():
    for phrase in ("¿qué hay hoy?", "what do I have today?"):
        window = query.parse_window(phrase, now=_NOW)
        assert window is not None
        start, end, label = window
        assert label in ("hoy", "today")
        local_start = start.astimezone()
        local_end = end.astimezone()
        assert local_start.hour == 0 and local_start.minute == 0
        assert (local_end - local_start) == timedelta(days=1)
        assert local_start.date() == _NOW.astimezone().date()


def test_parse_window_manana_tomorrow():
    for phrase in ("¿algo mañana?", "what's on tomorrow?"):
        window = query.parse_window(phrase, now=_NOW)
        assert window is not None
        start, _end, _label = window
        local_start = start.astimezone()
        assert local_start.date() == (_NOW.astimezone().date() + timedelta(days=1))


def test_parse_window_pasado_manana_day_after_tomorrow():
    for phrase in ("pasado mañana tengo algo?", "what about the day after tomorrow"):
        window = query.parse_window(phrase, now=_NOW)
        assert window is not None
        start, _end, _label = window
        local_start = start.astimezone()
        assert local_start.date() == (_NOW.astimezone().date() + timedelta(days=2))


def test_parse_window_every_weekday_es_and_en():
    # _NOW is a Wednesday (weekday()==2). "el miércoles" today should resolve
    # to TODAY (weekday matching counts today), every other day the NEXT one.
    es_days = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    en_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    today_local_date = _NOW.astimezone().date()
    for idx, (es, en) in enumerate(zip(es_days, en_days)):
        for phrase in (f"qué hay el {es}", f"what do I have {en}"):
            window = query.parse_window(phrase, now=_NOW)
            assert window is not None, phrase
            start, end, _label = window
            local_start = start.astimezone()
            days_ahead = (idx - _NOW.astimezone().weekday()) % 7
            assert local_start.date() == today_local_date + timedelta(days=days_ahead)
            assert (end - start) == timedelta(days=1)


def test_parse_window_el_viernes_exact_utc_bounds():
    """The one worked example: a fixed 'now' → 'el viernes' → the exact UTC window."""
    window = query.parse_window("¿qué hay en mi calendario el viernes?", now=_NOW)
    assert window is not None
    start, end, label = window
    assert label == "el viernes"
    local_now = _NOW.astimezone()
    days_ahead = (4 - local_now.weekday()) % 7  # Friday index = 4
    expected_local_start = datetime(
        local_now.year, local_now.month, local_now.day, tzinfo=local_now.tzinfo
    ) + timedelta(days=days_ahead)
    assert start == expected_local_start.astimezone(timezone.utc)
    assert end == start + timedelta(days=1)


def test_parse_window_esta_semana_this_week():
    for phrase in ("mi agenda de esta semana", "what's on this week"):
        window = query.parse_window(phrase, now=_NOW)
        assert window is not None
        start, end, _label = window
        # start == now (not truncated to midnight)
        assert start == _NOW
        local_end = end.astimezone()
        assert local_end.hour == 0 and local_end.minute == 0
        # end is a Sunday-night boundary, i.e. the following Monday 00:00.
        assert local_end.date().weekday() == 0
        assert local_end.date() > _NOW.astimezone().date()


def test_parse_window_weekend():
    for phrase in ("qué tengo este fin de semana", "plans this weekend?"):
        window = query.parse_window(phrase, now=_NOW)
        assert window is not None
        start, end, _label = window
        local_start = start.astimezone()
        local_end = end.astimezone()
        assert local_start.date().weekday() == 5  # Saturday
        assert (local_end - local_start) == timedelta(days=2)


def test_parse_window_next_week():
    for phrase in ("la próxima semana tengo clases", "what about next week"):
        window = query.parse_window(phrase, now=_NOW)
        assert window is not None
        start, end, _label = window
        local_start = start.astimezone()
        local_end = end.astimezone()
        assert local_start.date().weekday() == 0  # Monday
        assert local_start.date() > _NOW.astimezone().date()
        assert (local_end - local_start) == timedelta(days=7)


def test_parse_window_none_when_no_time_reference():
    assert query.parse_window("cómo estás", now=_NOW) is None
    assert query.parse_window("", now=_NOW) is None
    assert query.parse_window(None, now=_NOW) is None


# ── is_calendar_query ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "qué hay en mi calendario el viernes",
        "what do I have tomorrow",
        "mi agenda de esta semana",
        "any events today?",
        "¿qué dice mi schedule mañana?",
    ],
)
def test_is_calendar_query_hits(text):
    assert query.is_calendar_query(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "qué hay de nuevo",
        "cómo estás",
        "noticias de hoy",
        "",
        None,
    ],
)
def test_is_calendar_query_near_misses(text):
    assert query.is_calendar_query(text) is False


# ── answer() ─────────────────────────────────────────────────────────────────


def _fake_events(start_utc):
    # ``start_utc`` is the parsed window's UTC start, which corresponds to LOCAL
    # midnight — adding a plain timedelta lands on the intended LOCAL clock time
    # regardless of the test machine's timezone (no .replace(hour=...), which
    # would set the UTC hour instead and drift under a non-UTC local offset).
    return [
        {
            "summary": "Algorithms 201",
            "start": start_utc + timedelta(hours=9),
            "end": start_utc + timedelta(hours=10),
            "location": "Room 204",
            "all_day": False,
            "calendar": "default",
        },
        {
            "summary": "Team Sync",
            "start": start_utc + timedelta(hours=13, minutes=30),
            "end": start_utc + timedelta(hours=15),
            "location": "Bldg A",
            "all_day": False,
            "calendar": "default",
        },
        {
            "summary": "Reunión",
            "start": start_utc + timedelta(hours=18),
            "end": start_utc + timedelta(hours=19),
            "location": "",
            "all_day": False,
            "calendar": "default",
        },
    ]


@pytest.mark.asyncio
async def test_answer_grouped_reply_for_friday_with_events(monkeypatch):
    window = query.parse_window("qué hay en mi calendario el viernes", now=_NOW)
    assert window is not None
    start, _end, _label = window
    events = _fake_events(start)

    async def _fake_upcoming(instance_id, *, hours=36, config=None):
        return events

    monkeypatch.setattr("lhassa.calendar.feed.upcoming", _fake_upcoming)
    monkeypatch.setattr(query, "parse_window", lambda text, **kw: window)

    reply = await query.answer("inst_test", "qué hay en mi calendario el viernes")
    assert reply is not None
    assert reply.startswith(f"El viernes {start.astimezone().day} tienes 3 cosas:")
    assert "📅 09:00 Algorithms 201 · Room 204" in reply
    assert "📅 13:30 Team Sync · Bldg A" in reply
    assert "📅 18:00 Reunión" in reply


@pytest.mark.asyncio
async def test_answer_empty_window_message_spanish(monkeypatch):
    window = query.parse_window("qué hay en mi calendario el viernes", now=_NOW)

    async def _fake_upcoming(instance_id, *, hours=36, config=None):
        return []

    monkeypatch.setattr("lhassa.calendar.feed.upcoming", _fake_upcoming)
    monkeypatch.setattr(query, "parse_window", lambda text, **kw: window)

    reply = await query.answer("inst_test", "qué hay en mi calendario el viernes")
    assert reply == "El viernes no tienes nada en tu calendario."


@pytest.mark.asyncio
async def test_answer_language_match_english(monkeypatch):
    window = query.parse_window("what do I have friday", now=_NOW)

    async def _fake_upcoming(instance_id, *, hours=36, config=None):
        return []

    monkeypatch.setattr("lhassa.calendar.feed.upcoming", _fake_upcoming)
    monkeypatch.setattr(query, "parse_window", lambda text, **kw: window)

    reply = await query.answer("inst_test", "what do I have friday")
    assert reply == "Friday you don't have anything on your calendar."


@pytest.mark.asyncio
async def test_answer_none_for_non_calendar_message(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("feed.upcoming must not be called for a non-calendar message")

    monkeypatch.setattr("lhassa.calendar.feed.upcoming", _boom)

    reply = await query.answer("inst_test", "cómo estás hoy en la mañana sin planes")
    # "cómo estás" isn't a calendar query at all (no keyword, no have/tengo/hay).
    assert reply is None
