"""Tests for lhassa.notify.capabilities; the self-routing consciousness's
shell half: the capability catalog, the [[routable:...]] marker, and the
route table (DATA, not code). Fully offline: no LLM, no network.
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


# ── match() / tag_intent() ────────────────────────────────────────────────────


def test_match_recognises_calendar_view():
    assert capabilities.match("what's on my calendar tomorrow?") == "calendar_view"


def test_match_returns_none_for_unrecognised_text():
    assert capabilities.match("tell me a joke") is None


def test_match_never_raises_on_malformed_input():
    assert capabilities.match(None) is None
    assert capabilities.match("") is None
    assert capabilities.match(123) is None  # type: ignore[arg-type]


def test_tag_intent_appends_marker_for_a_recognised_capability():
    assert capabilities.tag_intent("any events on my calendar?") == " [routable:calendar_view]"


def test_tag_intent_is_empty_for_an_unrecognised_message():
    assert capabilities.tag_intent("how's the weather feeling today, existentially") == ""


def test_get_capability_returns_none_for_unknown_name():
    assert capabilities.get_capability("does_not_exist") is None
    assert capabilities.get_capability("calendar_view") is not None


# ── the route table (DATA) ────────────────────────────────────────────────────


def test_add_route_refuses_unknown_capability(tmp_path):
    cfg = _cfg(tmp_path)
    assert capabilities.add_route("not_a_real_capability", config=cfg) is False
    assert capabilities.load_routes(cfg) == {}


def test_add_route_then_is_routed(tmp_path):
    cfg = _cfg(tmp_path)
    assert capabilities.is_routed("calendar_view", config=cfg) is False
    assert capabilities.add_route("calendar_view", config=cfg) is True
    assert capabilities.is_routed("calendar_view", config=cfg) is True


def test_remove_route(tmp_path):
    cfg = _cfg(tmp_path)
    capabilities.add_route("calendar_view", config=cfg)
    assert capabilities.remove_route("calendar_view", config=cfg) is True
    assert capabilities.is_routed("calendar_view", config=cfg) is False
    # removing an already-absent route reports False, doesn't raise.
    assert capabilities.remove_route("calendar_view", config=cfg) is False


def test_load_routes_degrades_to_empty_on_malformed_file(tmp_path):
    path = tmp_path / "routes.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    cfg = _cfg(tmp_path)
    assert capabilities.load_routes(cfg) == {}


def test_apply_approved_route_applies_the_capability_from_a_payload(tmp_path):
    cfg = _cfg(tmp_path)
    ok = capabilities.apply_approved_route({"capability": "calendar_view"}, config=cfg)
    assert ok is True
    assert capabilities.is_routed("calendar_view", config=cfg)


def test_apply_approved_route_refuses_a_missing_capability(tmp_path):
    cfg = _cfg(tmp_path)
    assert capabilities.apply_approved_route({}, config=cfg) is False
    assert capabilities.load_routes(cfg) == {}


# ── apply_route(): the $0 serving path ────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_route_returns_none_when_unrecognised(tmp_path):
    cfg = _cfg(tmp_path)
    assert await capabilities.apply_route("tell me a joke", config=cfg) is None


@pytest.mark.asyncio
async def test_apply_route_returns_none_when_recognised_but_not_routed(tmp_path):
    cfg = _cfg(tmp_path)
    # is_calendar_query recognises it, but no route has been turned on yet.
    result = await capabilities.apply_route("any events on my calendar?", config=cfg)
    assert result is None


@pytest.mark.asyncio
async def test_apply_route_serves_a_routed_capability(tmp_path, monkeypatch):
    """Once routed, apply_route calls the capability's own handler and serves
    its answer; $0, no model. The handler is monkeypatched so this test stays
    a pure unit test of the routing mechanism, not the calendar seam itself."""
    cfg = _cfg(tmp_path)
    capabilities.add_route("calendar_view", config=cfg)

    async def _fake_handler(text, *, config=None):
        return "You have nothing on your calendar."

    cap = capabilities.get_capability("calendar_view")
    monkeypatch.setattr(cap, "handler", _fake_handler)

    result = await capabilities.apply_route("what's on my calendar today?", config=cfg)
    assert result == "You have nothing on your calendar."


@pytest.mark.asyncio
async def test_apply_route_degrades_to_none_when_handler_raises(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    capabilities.add_route("calendar_view", config=cfg)

    async def _boom(text, *, config=None):
        raise RuntimeError("boom")

    cap = capabilities.get_capability("calendar_view")
    monkeypatch.setattr(cap, "handler", _boom)

    result = await capabilities.apply_route("what's on my calendar today?", config=cfg)
    assert result is None


@pytest.mark.asyncio
async def test_apply_route_degrades_to_none_when_handler_cannot_resolve(tmp_path):
    """A recognised-and-routed capability whose handler still can't answer
    (e.g. the calendar seam has no parseable time reference) degrades to None
   ; the caller falls through to its normal path, exactly as before routing."""
    cfg = _cfg(tmp_path)
    capabilities.add_route("calendar_view", config=cfg)

    result = await capabilities.apply_route("any events on my calendar?", config=cfg)
    assert result is None


# ── is_followup(); the connector / elliptical-fragment detector ────────────


@pytest.mark.parametrize(
    "text",
    [
        "Y el sabado?",
        "¿y en marzo?",
        "también el lunes",
        "and Saturday?",
        "what about Monday?",
        "and the following week?",
        "o el domingo",
    ],
)
def test_is_followup_hits(text):
    assert capabilities.is_followup(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "hola",
        "cómo estás",
        "qué tiempo hace en Madrid",
        "",
        None,
        "gracias",
        "tell me a joke about pirates",
    ],
)
def test_is_followup_near_misses(text):
    assert capabilities.is_followup(text) is False


def test_is_followup_never_raises_on_malformed_input():
    assert capabilities.is_followup(123) is False  # type: ignore[arg-type]


def test_is_followup_true_even_for_a_connector_message_with_no_date():
    """is_followup is a SIGNAL, not a semantic guarantee; a connector-led
    message that isn't really about the sticky topic still reads as a
    follow-up; it's the capability's own continue_followup that declines."""
    assert capabilities.is_followup("y además necesito comprar leche") is True


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
