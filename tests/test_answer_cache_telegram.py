"""Tests for the answer-cache hook in ``notify.telegram_inbound._brain_reply``.

Exercises the real cache (real, tmp-redirected core DB; see conftest's autouse
``_isolate_brain_db``) and the real deterministic intent classifier; only the
model provider, memory-load, and router are faked (the same style as
``test_telegram_inbound.py``), so a cache hit/miss/never-cached assertion is
exercised end to end through ``_brain_reply`` itself.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.core import db
from lhassa.notify import telegram_inbound


@pytest.fixture(autouse=True)
def _fresh_chat_state():
    """Module-level chat history/turn counters never leak between tests."""
    telegram_inbound._CHAT_HISTORY.clear()
    telegram_inbound._CHAT_TURNS.clear()
    yield
    telegram_inbound._CHAT_HISTORY.clear()
    telegram_inbound._CHAT_TURNS.clear()


class _FakeProvider:
    """Records every ``complete()`` call so a test can assert it did/didn't run."""

    def __init__(self, reply="ok"):
        self.reply = reply
        self.calls = []
        self.name = "fake"

    async def complete(self, messages, *, model=None, max_tokens=None, system=None):
        self.calls.append({"messages": messages, "model": model})
        return SimpleNamespace(text=self.reply, cost_usd=0.001)


def _cfg(**over):
    base = dict(
        telegram_provider="",
        answer_cache_enabled=True,
        answer_cache_threshold=0.90,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _patch_common(monkeypatch, provider):
    """Fake memory-load + router.decide + no fallback chain (mirrors
    ``test_telegram_inbound._patch_brain`` minus the DB/episode no-ops; this
    module wants the REAL db so the cache table/rows actually persist)."""
    import lhassa.llm.router as router_mod
    import lhassa.memory.load as load_mod

    async def _fake_load(instance_id=None, **kwargs):
        return SimpleNamespace(to_prompt=lambda: "")

    def _fake_decide(trigger, ctx=None, *, config, **kwargs):
        return SimpleNamespace(provider=provider, model="cheap", reason="routine:telegram")

    monkeypatch.setattr(load_mod, "load", _fake_load)
    monkeypatch.setattr(router_mod, "decide", _fake_decide)
    monkeypatch.setattr(telegram_inbound, "_fallback_chain", lambda config, primary: [])


def _last_episode(instance_id="inst_personal"):
    rows = asyncio.run(db.load_recent_episodes(instance_id, n=1))
    return rows[0] if rows else None


def test_cacheable_question_asked_twice_second_ask_is_free(monkeypatch):
    provider = _FakeProvider(reply="Paris.")
    _patch_common(monkeypatch, provider)
    cfg = _cfg()
    text = "What is the capital of France?"

    reply1 = asyncio.run(telegram_inbound._brain_reply(text, config=cfg))
    assert reply1 == "Paris."
    assert len(provider.calls) == 1
    ep1 = _last_episode()
    # Plan 4 Stream 1 prefixes the routing trajectory (type/mode/effort) before
    # the "think:"/"cache-hit:" label; check containment, not a strict prefix.
    assert "think:" in ep1["decision"]
    assert ep1["costo_usd"] == 0.001

    # Second identical ask: served from the cache, $0, provider NOT called again.
    reply2 = asyncio.run(telegram_inbound._brain_reply(text, config=cfg))
    assert reply2 == "Paris."
    assert len(provider.calls) == 1  # unchanged; no second think
    ep2 = _last_episode()
    assert "cache-hit:" in ep2["decision"]
    assert ep2["costo_usd"] == 0.0
    assert ep2["resultado"] == "Paris."


def test_live_data_question_is_never_cached(monkeypatch):
    provider = _FakeProvider(reply="It's sunny today.")
    _patch_common(monkeypatch, provider)
    cfg = _cfg()
    text = "What's the weather today?"

    reply1 = asyncio.run(telegram_inbound._brain_reply(text, config=cfg))
    assert reply1 == "It's sunny today."
    assert len(provider.calls) == 1

    reply2 = asyncio.run(telegram_inbound._brain_reply(text, config=cfg))
    assert reply2 == "It's sunny today."
    # A live_data question always thinks again; never served from cache.
    assert len(provider.calls) == 2
    ep2 = _last_episode()
    assert "think:" in ep2["decision"]


def test_judgment_question_always_thinks(monkeypatch):
    provider = _FakeProvider(reply="I'm doing well, thanks!")
    _patch_common(monkeypatch, provider)
    cfg = _cfg()
    text = "How are you feeling today about your progress?"
    # Note: contains "today" (live_data) deliberately avoided below; use a
    # judgment-shaped question instead.
    text = "I think we should refactor this module, what do you think?"

    reply1 = asyncio.run(telegram_inbound._brain_reply(text, config=cfg))
    assert reply1 == "I'm doing well, thanks!"
    assert len(provider.calls) == 1

    reply2 = asyncio.run(telegram_inbound._brain_reply(text, config=cfg))
    assert reply2 == "I'm doing well, thanks!"
    # Judgment always thinks; never served from cache.
    assert len(provider.calls) == 2


def test_answer_cache_disabled_always_thinks(monkeypatch):
    provider = _FakeProvider(reply="Paris.")
    _patch_common(monkeypatch, provider)
    cfg = _cfg(answer_cache_enabled=False)
    text = "What is the capital of France?"

    asyncio.run(telegram_inbound._brain_reply(text, config=cfg))
    asyncio.run(telegram_inbound._brain_reply(text, config=cfg))
    assert len(provider.calls) == 2  # cache never consulted/written


def test_cache_hit_still_records_rolling_history(monkeypatch):
    provider = _FakeProvider(reply="Paris.")
    _patch_common(monkeypatch, provider)
    cfg = _cfg()
    text = "What is the capital of France?"

    asyncio.run(telegram_inbound._brain_reply(text, config=cfg))
    asyncio.run(telegram_inbound._brain_reply(text, config=cfg))

    history = telegram_inbound._history_messages(cfg)
    # Two full turns (user+assistant) recorded even though the second was a
    # $0 cache hit; a cache-served reply is still part of the conversation.
    assert len(history) == 4
    assert history[-1]["content"] == "Paris."
    assert history[-1]["role"] == "assistant"
