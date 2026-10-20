"""The calendar pack's ``calendar_view`` capability: recognition, the route
table, and the elliptical follow-up continuation. Split from core's
``tests/test_notify_capabilities.py``, which now tests the catalog mechanism
against a stand-in. Fully offline: no LLM, no network.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from lhassa.notify import capabilities


def _cfg(tmp_path, **over):
    base = dict(
        instance_id="inst_test",
        routes_file=str(tmp_path / "routes.json"),
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_the_pack_registers_calendar_view():
    assert capabilities.get_capability("calendar_view") is not None


def test_match_recognises_calendar_view():
    assert capabilities.match("what's on my calendar tomorrow?") == "calendar_view"


def test_tag_intent_appends_the_calendar_marker():
    assert capabilities.tag_intent("any events on my calendar?") == " [routable:calendar_view]"


def test_calendar_view_can_be_routed(tmp_path):
    cfg = _cfg(tmp_path)
    assert capabilities.add_route("calendar_view", config=cfg) is True
    assert capabilities.is_routed("calendar_view", config=cfg) is True


@pytest.mark.asyncio
async def test_apply_route_returns_none_when_recognised_but_not_routed(tmp_path):
    cfg = _cfg(tmp_path)
    # is_calendar_query recognises it, but no route has been turned on yet.
    result = await capabilities.apply_route("any events on my calendar?", config=cfg)
    assert result is None


@pytest.mark.asyncio
async def test_apply_route_degrades_to_none_when_handler_cannot_resolve(tmp_path):
    """Routed, but the calendar seam has no parseable time reference: None."""
    cfg = _cfg(tmp_path)
    capabilities.add_route("calendar_view", config=cfg)

    result = await capabilities.apply_route("any events on my calendar?", config=cfg)
    assert result is None


def test_topic_context_carries_the_resolved_day():
    cap = capabilities.get_capability("calendar_view")
    ctx = capabilities.topic_context(cap, "what's on my calendar tomorrow?")
    assert set(ctx) == {"date"}


# ── calendar_view.continue_followup ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_calendar_continue_followup_parses_and_answers(monkeypatch):
    """The calendar continuation bypasses is_calendar_query's keyword gate;
    "Y el sabado?" has no calendar keyword and no have/tengo/hay word; and
    answers straight from the feed via answer_window."""
    from lhassa.calendar import feed as feed_mod
    from lhassa.calendar import query as query_mod

    window = query_mod.parse_window("el sabado")
    assert window is not None
    start, _end, _label = window

    async def _fake_upcoming(instance_id, *, hours=36, since=None, config=None):
        return [
            {
                "summary": "Mercado",
                "start": start + timedelta(hours=10),
                "end": start + timedelta(hours=11),
                "location": None,
                "all_day": False,
                "calendar": "default",
            }
        ]

    monkeypatch.setattr(feed_mod, "upcoming", _fake_upcoming)

    cap = capabilities.get_capability("calendar_view")
    assert cap.continue_followup is not None
    # "Y el sabado?" has no calendar keyword and no have/tengo/hay word; the
    # plain recognizer/handler would refuse it; the continuation must not.
    assert query_mod.is_calendar_query("Y el sabado?") is False

    reply = await cap.continue_followup("Y el sabado?", {}, config=None)
    assert reply is not None
    assert "Mercado" in reply


@pytest.mark.asyncio
async def test_calendar_continue_followup_declines_with_no_time_reference():
    cap = capabilities.get_capability("calendar_view")
    reply = await cap.continue_followup("y además necesito comprar leche", {}, config=None)
    assert reply is None


@pytest.mark.asyncio
async def test_calendar_continue_followup_bare_time_uses_sticky_day(monkeypatch):
    """A bare-time follow-up ("¿y a las 6am?") has no DAY reference, so
    answer_window declines; the continuation must fall back to
    answer_time_of_day, resolved against the sticky day in context, and find
    the morning event instead of falling through to a think."""
    from datetime import datetime, timezone

    from lhassa.calendar import feed as feed_mod

    local_now = datetime.now(timezone.utc).astimezone()
    day_start = datetime(
        local_now.year, local_now.month, local_now.day, tzinfo=local_now.tzinfo
    )
    six_utc = (day_start.replace(hour=6)).astimezone(timezone.utc)

    async def _fake_upcoming(instance_id, *, hours=36, since=None, config=None):
        return [
            {
                "summary": "Algorithms 201",
                "start": six_utc,
                "end": six_utc + timedelta(hours=2),
                "location": "Room 204",
                "all_day": False,
                "calendar": "default",
            }
        ]

    monkeypatch.setattr(feed_mod, "upcoming", _fake_upcoming)

    cap = capabilities.get_capability("calendar_view")
    ctx = {"date": local_now.date().isoformat()}
    reply = await cap.continue_followup("¿y a las 6am?", ctx, config=None)
    assert reply is not None
    assert "Algorithms 201" in reply
    assert reply.startswith("A las 06:00 tienes:")
