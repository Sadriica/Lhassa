"""Tests for lhassa.notify.capabilities; the self-routing consciousness's
shell half: the capability catalog, the [[routable:...]] marker, and the
route table (DATA, not code). Fully offline: no LLM, no network.

Core ships the catalog empty (the calendar pack registers ``calendar_view``;
its own tests live in ``packages/lhassa-calendar``), so these mechanism tests
run against a stand-in ``probe_view`` capability.
"""

from types import SimpleNamespace

import pytest

from lhassa.notify import capabilities


async def _probe_handler(text, *, config=None):
    return None


@pytest.fixture(autouse=True)
def _probe_catalog(monkeypatch):
    """A one-entry catalog: ``probe_view`` recognises any text naming a probe."""
    probe = capabilities.Capability(
        "probe_view",
        lambda text: "probe" in text.lower(),
        "A stand-in capability for the mechanism tests.",
        _probe_handler,
    )
    monkeypatch.setattr(capabilities, "CAPABILITIES", [probe])
    return probe


def _cfg(tmp_path, **over):
    base = dict(
        instance_id="inst_test",
        routes_file=str(tmp_path / "routes.json"),
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── match() / tag_intent() ────────────────────────────────────────────────────


def test_match_recognises_a_registered_capability():
    assert capabilities.match("what's on my probe tomorrow?") == "probe_view"


def test_match_is_none_with_an_empty_catalog(monkeypatch):
    """Core alone (no pack registered anything) recognises nothing."""
    monkeypatch.setattr(capabilities, "CAPABILITIES", [])
    assert capabilities.match("what's on my calendar tomorrow?") is None
    assert capabilities.tag_intent("what's on my calendar tomorrow?") == ""


def test_register_capability_is_idempotent(_probe_catalog):
    again = capabilities.Capability(
        "probe_view", lambda text: True, "duplicate", _probe_handler
    )
    assert capabilities.register_capability(again) is _probe_catalog
    assert len(capabilities.CAPABILITIES) == 1


def test_topic_context_degrades_to_empty(_probe_catalog):
    assert capabilities.topic_context(None, "x") == {}
    assert capabilities.topic_context(_probe_catalog, "x") == {}


def test_match_returns_none_for_unrecognised_text():
    assert capabilities.match("tell me a joke") is None


def test_match_never_raises_on_malformed_input():
    assert capabilities.match(None) is None
    assert capabilities.match("") is None
    assert capabilities.match(123) is None  # type: ignore[arg-type]


def test_tag_intent_appends_marker_for_a_recognised_capability():
    assert capabilities.tag_intent("any events on my probe?") == " [routable:probe_view]"


def test_tag_intent_is_empty_for_an_unrecognised_message():
    assert capabilities.tag_intent("how's the weather feeling today, existentially") == ""


def test_get_capability_returns_none_for_unknown_name():
    assert capabilities.get_capability("does_not_exist") is None
    assert capabilities.get_capability("probe_view") is not None


# ── the route table (DATA) ────────────────────────────────────────────────────


def test_add_route_refuses_unknown_capability(tmp_path):
    cfg = _cfg(tmp_path)
    assert capabilities.add_route("not_a_real_capability", config=cfg) is False
    assert capabilities.load_routes(cfg) == {}


def test_add_route_then_is_routed(tmp_path):
    cfg = _cfg(tmp_path)
    assert capabilities.is_routed("probe_view", config=cfg) is False
    assert capabilities.add_route("probe_view", config=cfg) is True
    assert capabilities.is_routed("probe_view", config=cfg) is True


def test_remove_route(tmp_path):
    cfg = _cfg(tmp_path)
    capabilities.add_route("probe_view", config=cfg)
    assert capabilities.remove_route("probe_view", config=cfg) is True
    assert capabilities.is_routed("probe_view", config=cfg) is False
    # removing an already-absent route reports False, doesn't raise.
    assert capabilities.remove_route("probe_view", config=cfg) is False


def test_load_routes_degrades_to_empty_on_malformed_file(tmp_path):
    path = tmp_path / "routes.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    cfg = _cfg(tmp_path)
    assert capabilities.load_routes(cfg) == {}


def test_apply_approved_route_applies_the_capability_from_a_payload(tmp_path):
    cfg = _cfg(tmp_path)
    ok = capabilities.apply_approved_route({"capability": "probe_view"}, config=cfg)
    assert ok is True
    assert capabilities.is_routed("probe_view", config=cfg)


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
    # probe_view recognises it, but no route has been turned on yet.
    result = await capabilities.apply_route("any events on my probe?", config=cfg)
    assert result is None


@pytest.mark.asyncio
async def test_apply_route_serves_a_routed_capability(tmp_path, monkeypatch):
    """Once routed, apply_route calls the capability's own handler and serves
    its answer; $0, no model."""
    cfg = _cfg(tmp_path)
    capabilities.add_route("probe_view", config=cfg)

    async def _fake_handler(text, *, config=None):
        return "Nothing on your probe."

    cap = capabilities.get_capability("probe_view")
    monkeypatch.setattr(cap, "handler", _fake_handler)

    result = await capabilities.apply_route("what's on my probe today?", config=cfg)
    assert result == "Nothing on your probe."


@pytest.mark.asyncio
async def test_apply_route_degrades_to_none_when_handler_raises(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    capabilities.add_route("probe_view", config=cfg)

    async def _boom(text, *, config=None):
        raise RuntimeError("boom")

    cap = capabilities.get_capability("probe_view")
    monkeypatch.setattr(cap, "handler", _boom)

    result = await capabilities.apply_route("what's on my probe today?", config=cfg)
    assert result is None


@pytest.mark.asyncio
async def test_apply_route_degrades_to_none_when_handler_cannot_resolve(tmp_path):
    """A recognised-and-routed capability whose handler still can't answer
    (the stand-in handler answers None) degrades to None; the caller falls
    through to its normal path, exactly as before routing."""
    cfg = _cfg(tmp_path)
    capabilities.add_route("probe_view", config=cfg)

    result = await capabilities.apply_route("any events on my probe?", config=cfg)
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
