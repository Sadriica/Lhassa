"""The calendar pack through the chat engine: the always-on calendar
shortcut, the ``[routable:calendar_view]`` marker and the sticky-topic
follow-up. Split from core's ``tests/test_chat_engine.py`` (which tests the
same seams against a stand-in capability). Played through
``tests.chat_fakes.FakeTransport``: no transport, no network, no LLM.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from tests.chat_fakes import FakeTransport


def _cfg(**over):
    base = dict(
        telegram_bot_token="TOKEN",
        telegram_chat_id="123",
        telegram_poll_enabled=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeProvider:
    """Records the tool-less completion calls the brain think makes."""

    def __init__(self, reply="brain reply", raise_exc=None):
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = []

    async def complete(self, messages, *, model=None, max_tokens=None, system=None):
        self.calls.append({"messages": messages, "model": model, "system": system})
        if self.raise_exc is not None:
            raise self.raise_exc
        return SimpleNamespace(text=self.reply, cost_usd=0.0)


def _patch_brain(monkeypatch, *, reply="brain reply", raise_exc=None):
    """Mock the tool-less brain think and forbid the 26-tool agent.

    Returns the fake provider so a test can assert it was (or wasn't) called.
    """
    import lhassa.cli.agent as agent_mod
    import lhassa.core.db as db_mod
    import lhassa.core.episode as episode_mod
    import lhassa.llm.router as router_mod
    import lhassa.memory.load as load_mod

    provider = _FakeProvider(reply=reply, raise_exc=raise_exc)

    async def _noop_init():
        return None

    async def _fake_load(instance_id=None, **kwargs):
        return SimpleNamespace(to_prompt=lambda: "")

    def _fake_decide(trigger, ctx=None, *, config, **kwargs):
        return SimpleNamespace(provider=provider, model="cheap", reason="routine:telegram")

    async def _noop_append(self):
        return "ep"

    async def _boom_agent(*a, **k):
        raise AssertionError("inbound must not call the 26-tool agent (run_once)")

    monkeypatch.setattr(db_mod, "init_db", _noop_init)
    monkeypatch.setattr(load_mod, "load", _fake_load)
    monkeypatch.setattr(router_mod, "decide", _fake_decide)
    monkeypatch.setattr(episode_mod.Episode, "append", _noop_append)
    monkeypatch.setattr(agent_mod, "run_once", _boom_agent)
    return provider


def _patch_send(monkeypatch):
    """A fresh recording transport; returns it (its ``sent`` holds the replies)."""
    return FakeTransport()


def _say(transport, text, cfg=None):
    asyncio.run(transport.say(text, config=cfg or _cfg()))


def test_calendar_query_answered_without_provider(monkeypatch):
    """A calendar question is answered from lhassa.calendar.query; deterministic,
    $0; and NEVER reaches the tool-less brain think (no provider.complete call).
    An episode is logged through the calendar seam."""
    provider = _patch_brain(monkeypatch)
    transport = _patch_send(monkeypatch)
    sent = transport.sent

    from lhassa.calendar import feed as feed_mod
    from lhassa.calendar import query as query_mod
    from lhassa.core import episode as episode_mod

    fixed_window = query_mod.parse_window("el viernes")
    assert fixed_window is not None
    start, _end, _label = fixed_window

    async def _fake_upcoming(instance_id, *, hours=36, since=None, config=None):
        return [
            {
                "summary": "Algorithms 201",
                "start": start + timedelta(hours=9),
                "end": start + timedelta(hours=10),
                "location": "Room 204",
                "all_day": False,
                "calendar": "default",
            }
        ]

    monkeypatch.setattr(feed_mod, "upcoming", _fake_upcoming)

    logged_episodes = []

    async def _capture_append(self):
        logged_episodes.append(self)
        return "ep"

    monkeypatch.setattr(episode_mod.Episode, "append", _capture_append)

    _say(transport, "¿qué hay en mi calendario el viernes?")

    assert provider.calls == []  # never reached the tool-less think
    assert sent and sent[0].startswith("El viernes")
    assert "Algorithms 201" in sent[0]

    assert len(logged_episodes) == 1
    ep = logged_episodes[0]
    assert ep.despertar_por == "telegram"
    assert ep.decision.startswith("calendar-query:")
    assert ep.accion == "calendar_view"
    assert ep.costo_usd == 0.0
    assert ep.resultado == sent[0]


def test_calendar_query_falls_through_for_normal_message(monkeypatch):
    """A normal message (no calendar keyword, no time reference) is unaffected;
    it still reaches the tool-less brain think exactly as before."""
    provider = _patch_brain(monkeypatch, reply="Muy bien, gracias.")
    transport = _patch_send(monkeypatch)
    sent = transport.sent

    from lhassa.calendar import feed as feed_mod

    async def _boom(*a, **k):
        raise AssertionError("a non-calendar message must never touch the feed")

    monkeypatch.setattr(feed_mod, "upcoming", _boom)

    _say(transport, "cómo estás?")

    assert len(provider.calls) == 1
    assert sent == ["Muy bien, gracias."]


def test_recognized_but_unrouted_capability_tags_the_think_episode(monkeypatch):
    """A message a capability recognises (calendar keyword, no time reference;
    the calendar seam itself can't resolve it) still falls through to the brain
    think, but the episode's decision is tagged '[routable:calendar_view]' so
    the consciousness (agents.repetition.detect_routable) can find it later."""
    provider = _patch_brain(monkeypatch, reply="Nothing pinned down yet.")
    transport = _patch_send(monkeypatch)
    sent = transport.sent

    from lhassa.calendar import query as query_mod

    # This message HAS the calendar keyword (so is_calendar_query is True) but
    # no parseable time reference; the calendar seam itself returns None.
    assert query_mod.is_calendar_query("any events on my calendar?")
    assert query_mod.parse_window("any events on my calendar?") is None

    import lhassa.core.episode as episode_mod

    logged = []

    async def _capture_append(self):
        logged.append(self)
        return "ep"

    monkeypatch.setattr(episode_mod.Episode, "append", _capture_append)

    _say(transport, "any events on my calendar?")

    assert len(provider.calls) == 1  # it DID reach the think; no route is on
    assert sent == ["Nothing pinned down yet."]
    assert len(logged) == 1
    assert "[routable:calendar_view]" in logged[0].decision


def test_calendar_followup_served_from_calendar_without_provider(monkeypatch):
    """The exact owner-reported bug: a calendar question sets the sticky topic,
    then a connector-led follow-up ("Y el sabado?") is served straight from the
    calendar seam; the provider is NEVER called for the follow-up turn, and
    the episode is tagged 'thread-continue: calendar_view' at $0."""
    provider = _patch_brain(monkeypatch)
    transport = _patch_send(monkeypatch)
    sent = transport.sent

    from lhassa.calendar import feed as feed_mod
    from lhassa.calendar import query as query_mod
    from lhassa.core import episode as episode_mod

    friday_window = query_mod.parse_window("el viernes")
    saturday_window = query_mod.parse_window("el sabado")
    assert friday_window is not None and saturday_window is not None
    fri_start, _, _ = friday_window
    sat_start, _, _ = saturday_window

    async def _fake_upcoming(instance_id, *, hours=36, since=None, config=None):
        return [
            {
                "summary": "Algorithms 201",
                "start": fri_start + timedelta(hours=9),
                "end": fri_start + timedelta(hours=10),
                "location": None,
                "all_day": False,
                "calendar": "default",
            },
            {
                "summary": "Mercado",
                "start": sat_start + timedelta(hours=10),
                "end": sat_start + timedelta(hours=11),
                "location": None,
                "all_day": False,
                "calendar": "default",
            },
        ]

    monkeypatch.setattr(feed_mod, "upcoming", _fake_upcoming)

    logged = []

    async def _capture_append(self):
        logged.append(self)
        return "ep"

    monkeypatch.setattr(episode_mod.Episode, "append", _capture_append)

    cfg = _cfg()

    # Turn 1: the calendar question; answered directly, sets the topic.
    _say(transport, "¿qué hay en mi calendario el viernes?", cfg)
    assert provider.calls == []
    assert len(sent) == 1
    assert "Algorithms 201" in sent[0]

    # Turn 2: the elliptical follow-up; must be served from the calendar too,
    # NOT the tool-less think.
    _say(transport, "Y el sabado?", cfg)

    assert provider.calls == []  # still never reached the brain think
    assert len(sent) == 2
    assert "Mercado" in sent[1]

    thread_episodes = [ep for ep in logged if ep.decision == "thread-continue: calendar_view"]
    assert len(thread_episodes) == 1
    assert thread_episodes[0].accion == "calendar_view"
    assert thread_episodes[0].costo_usd == 0.0


def test_connector_message_that_is_not_really_a_followup_falls_through(monkeypatch):
    """A connector-led message that doesn't carry a resolvable continuation for
    the active capability (no date reference at all) declines (continue_followup
    returns None) and falls through to the normal think; it must not force a
    wrong calendar answer."""
    provider = _patch_brain(monkeypatch, reply="Got it, noted.")
    transport = _patch_send(monkeypatch)
    sent = transport.sent

    from lhassa.calendar import feed as feed_mod
    from lhassa.calendar import query as query_mod
    from lhassa.core import episode as episode_mod

    friday_window = query_mod.parse_window("el viernes")
    assert friday_window is not None
    fri_start, _, _ = friday_window

    async def _fake_upcoming(instance_id, *, hours=36, since=None, config=None):
        return [
            {
                "summary": "Algorithms 201",
                "start": fri_start + timedelta(hours=9),
                "end": fri_start + timedelta(hours=10),
                "location": None,
                "all_day": False,
                "calendar": "default",
            }
        ]

    monkeypatch.setattr(feed_mod, "upcoming", _fake_upcoming)

    logged = []

    async def _capture_append(self):
        logged.append(self)
        return "ep"

    monkeypatch.setattr(episode_mod.Episode, "append", _capture_append)

    cfg = _cfg()

    _say(transport, "¿qué hay en mi calendario el viernes?", cfg)
    assert provider.calls == []

    _say(transport, "y además necesito comprar leche", cfg)

    assert len(provider.calls) == 1  # the continuation declined; fell through
    assert sent[-1] == "Got it, noted."
    assert not any(ep.decision == "thread-continue: calendar_view" for ep in logged)
