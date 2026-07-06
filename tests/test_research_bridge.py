"""The Telegram research bridge — the [[research: …]] marker + research_reply.

The chat think is tool-less; these tests pin the two halves that make its "I'll
look it up" promise REAL: the poller turns the marker into a minted task (and
never shows the marker), and the action searches + delivers with sources.
All offline: search and delivery are monkeypatched fakes.
"""

import asyncio
from types import SimpleNamespace

from lhassa.notify import telegram_inbound


def _cfg(**over):
    base = dict(instance_id="inst_personal")
    base.update(over)
    return SimpleNamespace(**base)


def _patch_mint(monkeypatch, *, raise_exc=None):
    calls = []

    async def fake_mint(instance_id, accion, objetivo="", params=None, *, origin, **kw):
        if raise_exc is not None:
            raise raise_exc
        calls.append(
            {"instance_id": instance_id, "accion": accion, "params": params, "origin": origin}
        )
        return ("task_1", "pendiente")

    async def fake_run_now(*a, **k):
        return True

    import lhassa.core.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "mint_task", fake_mint)
    monkeypatch.setattr(dispatch_mod, "run_task_now", fake_run_now)
    return calls


# ── the marker → mint half ─────────────────────────────────────────────────────


def test_marker_mints_a_research_task_and_is_stripped(monkeypatch):
    calls = _patch_mint(monkeypatch)
    reply = asyncio.run(
        telegram_inbound._apply_research_marker(
            "Voy a buscarlo.\n[[research: China AI satellite launch 2026]]",
            instance_id="inst_personal",
            config=_cfg(),
        )
    )
    assert len(calls) == 1
    assert calls[0]["accion"] == "research_reply"
    assert calls[0]["params"] == {"query": "China AI satellite launch 2026"}
    assert "[[" not in reply and "research:" not in reply
    assert "Voy a buscarlo." in reply
    assert telegram_inbound._RESEARCH_ACK in reply


def test_marker_only_reply_gets_the_ack_as_body(monkeypatch):
    _patch_mint(monkeypatch)
    reply = asyncio.run(
        telegram_inbound._apply_research_marker(
            "[[research: latest python release]]",
            instance_id="inst_personal",
            config=_cfg(),
        )
    )
    assert reply == telegram_inbound._RESEARCH_ACK


def test_no_marker_passes_through_untouched(monkeypatch):
    calls = _patch_mint(monkeypatch)
    text = "The wheel turns; nothing to look up."
    reply = asyncio.run(
        telegram_inbound._apply_research_marker(
            text, instance_id="inst_personal", config=_cfg()
        )
    )
    assert reply == text
    assert calls == []


def test_mint_failure_degrades_without_a_false_promise(monkeypatch):
    _patch_mint(monkeypatch, raise_exc=RuntimeError("db down"))
    reply = asyncio.run(
        telegram_inbound._apply_research_marker(
            "Déjame ver.\n[[research: something]]",
            instance_id="inst_personal",
            config=_cfg(),
        )
    )
    # The marker is gone, the visible text survives, and — crucially — the
    # follow-up promise is NOT made (the task was never minted).
    assert "[[" not in reply
    assert "Déjame ver." in reply
    assert telegram_inbound._RESEARCH_ACK not in reply


def test_marker_query_is_capped_and_whitespace_collapsed(monkeypatch):
    calls = _patch_mint(monkeypatch)
    long_query = "palabra " * 100
    asyncio.run(
        telegram_inbound._apply_research_marker(
            f"[[research: {long_query}]]", instance_id="inst_personal", config=_cfg()
        )
    )
    q = calls[0]["params"]["query"]
    assert len(q) <= telegram_inbound._RESEARCH_MAX_QUERY
    assert "  " not in q


# ── the action half ────────────────────────────────────────────────────────────


def _patch_search(monkeypatch, results):
    async def fake_search(query, max_results=4):
        if isinstance(results, Exception):
            raise results
        return results

    import lhassa.tools.search as search_mod

    monkeypatch.setattr(search_mod, "web_search", fake_search)


def _patch_send(monkeypatch):
    sent = []

    async def fake_send(text, *, config=None):
        sent.append(text)
        return True

    import lhassa.notify.telegram as telegram_mod

    monkeypatch.setattr(telegram_mod, "send", fake_send)
    return sent


def test_research_reply_sends_sourced_results(monkeypatch):
    from lhassa.actionpack._research import research_reply

    _patch_search(
        monkeypatch,
        [
            {"title": "China launches AI satellite", "body": "The launch...", "url": "https://example.com/a"},
            {"title": "Analysis", "body": "What it means", "url": "https://example.com/b"},
        ],
    )
    sent = _patch_send(monkeypatch)

    out = asyncio.run(research_reply({"query": "china ai satellite"}))
    assert "2 result(s) sent" in out
    assert len(sent) == 1
    assert "China launches AI satellite" in sent[0]
    assert "https://example.com/a" in sent[0]


def test_research_reply_search_failure_still_messages_the_owner(monkeypatch):
    from lhassa.actionpack._research import research_reply

    _patch_search(monkeypatch, RuntimeError("no network"))
    sent = _patch_send(monkeypatch)

    out = asyncio.run(research_reply({"query": "anything"}))
    assert "0 result(s) sent" in out
    assert len(sent) == 1  # the promised follow-up ALWAYS arrives
    assert "couldn't reach a search backend" in sent[0]


def test_research_reply_no_query_is_a_noop(monkeypatch):
    from lhassa.actionpack._research import research_reply

    sent = _patch_send(monkeypatch)
    out = asyncio.run(research_reply({}))
    assert out == "research_reply: no query"
    assert sent == []
