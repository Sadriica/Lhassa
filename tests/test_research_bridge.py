"""The Telegram research bridge; the [[research: …]] marker + research_reply.

The chat think is tool-less; these tests pin the two halves that make its "I'll
look it up" promise REAL: the poller turns the marker into a minted task (and
never shows the marker), and the action searches + delivers with sources.
All offline: search and delivery are monkeypatched fakes.
"""

import asyncio
from types import SimpleNamespace

from lhassa.chat import markers


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
        markers.apply_research_marker(
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
    assert markers._RESEARCH_ACK in reply


def test_marker_only_reply_gets_the_ack_as_body(monkeypatch):
    _patch_mint(monkeypatch)
    reply = asyncio.run(
        markers.apply_research_marker(
            "[[research: latest python release]]",
            instance_id="inst_personal",
            config=_cfg(),
        )
    )
    assert reply == markers._RESEARCH_ACK


def test_no_marker_passes_through_untouched(monkeypatch):
    calls = _patch_mint(monkeypatch)
    text = "The wheel turns; nothing to look up."
    reply = asyncio.run(
        markers.apply_research_marker(
            text, instance_id="inst_personal", config=_cfg()
        )
    )
    assert reply == text
    assert calls == []


def test_mint_failure_degrades_without_a_false_promise(monkeypatch):
    _patch_mint(monkeypatch, raise_exc=RuntimeError("db down"))
    reply = asyncio.run(
        markers.apply_research_marker(
            "Déjame ver.\n[[research: something]]",
            instance_id="inst_personal",
            config=_cfg(),
        )
    )
    # The marker is gone, the visible text survives, and (crucially) the
    # follow-up promise is NOT made (the task was never minted).
    assert "[[" not in reply
    assert "Déjame ver." in reply
    assert markers._RESEARCH_ACK not in reply


def test_marker_query_is_capped_and_whitespace_collapsed(monkeypatch):
    calls = _patch_mint(monkeypatch)
    long_query = "palabra " * 100
    asyncio.run(
        markers.apply_research_marker(
            f"[[research: {long_query}]]", instance_id="inst_personal", config=_cfg()
        )
    )
    q = calls[0]["params"]["query"]
    assert len(q) <= markers._RESEARCH_MAX_QUERY
    assert "  " not in q


# ── the action half ────────────────────────────────────────────────────────────


def _patch_search(monkeypatch, results):
    queries = []

    async def fake_search(query, max_results=4):
        queries.append(query)
        if isinstance(results, Exception):
            raise results
        if isinstance(results, dict):  # per-query results (retry tests)
            return results.get(query, [])
        return results

    async def fake_news(query, max_results=4, *, timelimit=None):
        key = f"news:{query}" + (f":{timelimit}" if timelimit else "")
        queries.append(key)
        if isinstance(results, dict):
            return results.get(key, [])
        return []

    import lhassa.tools.search as search_mod

    monkeypatch.setattr(search_mod, "web_search", fake_search)
    monkeypatch.setattr(search_mod, "news_search", fake_news)
    return queries


def _patch_send(monkeypatch):
    # research_reply goes through org.deliver -> notify.dispatch, which only fans
    # out to a channel that reports itself configured: a recording "telegram"
    # channel (configured, no real credentials, no pack needed) captures it.
    from tests.chat_fakes import RecordingChannel, install_channel

    return install_channel(monkeypatch, RecordingChannel("telegram")).sent


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


def test_research_reply_delivers_through_org_deliver_root_hop(monkeypatch):
    """research_reply now sends via ``org.deliver.deliver`` (an empty
    ``origin_department``: research is Lhassa's own hand, not delegated work);
    a single root-attributed ``org-deliver`` Episode should land alongside the
    Telegram send, same as ``send_reminder``."""
    from lhassa.actionpack._research import research_reply
    from lhassa.core import db
    from lhassa.org import topology as topo_mod
    from lhassa.org.topology import BrainNode, Topology

    monkeypatch.setattr(
        topo_mod, "load_topology", lambda config=None: Topology([BrainNode(id="lhassa")])
    )

    _patch_search(
        monkeypatch,
        [{"title": "China launches AI satellite", "body": "…", "url": "https://example.com/a"}],
    )
    sent = _patch_send(monkeypatch)

    out = asyncio.run(research_reply({"query": "china ai satellite"}))
    assert "1 result(s) sent" in out
    assert len(sent) == 1  # Telegram-only, exactly as telegram.send used to deliver

    rows = asyncio.run(db.load_recent_episodes("inst_personal", n=100))
    eps = [r for r in rows if r["despertar_por"] == "org-deliver"]
    assert eps and eps[0]["dominio"] == "lhassa"
    assert "delivered:" in eps[0]["resultado"]
    assert "ok)" in eps[0]["resultado"]


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


def test_research_reply_retries_with_a_date_anchor(monkeypatch):
    """A vague query that finds nothing is retried once anchored to month/year."""
    from datetime import datetime, timezone

    from lhassa.actionpack._research import research_reply

    anchored = f"noticias de hoy {datetime.now(timezone.utc).strftime('%B %d %Y')}"
    queries = _patch_search(
        monkeypatch,
        {anchored: [{"title": "Portada", "body": "…", "url": "https://example.com/n"}]},
    )
    sent = _patch_send(monkeypatch)

    out = asyncio.run(research_reply({"query": "noticias de hoy"}))
    assert queries == ["news:noticias de hoy:d", "noticias de hoy", anchored]
    assert "1 result(s) sent" in out
    assert anchored in sent[0]  # the digest names the query that actually worked


def test_research_reply_found_nothing_says_so_honestly(monkeypatch):
    """Zero results with a WORKING backend never claims the backend was down."""
    from lhassa.actionpack._research import research_reply

    _patch_search(monkeypatch, [])
    sent = _patch_send(monkeypatch)

    asyncio.run(research_reply({"query": "xyzzy"}))
    assert len(sent) == 1
    assert "found nothing useful" in sent[0]
    assert "search backend" not in sent[0]


def test_news_intent_routes_to_the_news_vertical_first(monkeypatch):
    """"noticias de hoy" is answerable because the NEWS vertical answers it;
    the routing a general search engine does internally."""
    from lhassa.actionpack._research import research_reply

    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    queries = _patch_search(
        monkeypatch,
        {
            "news:noticias de hoy:d": [
                {"title": "Portada", "body": "…", "url": "https://example.com/n",
                 "source": "El Tiempo", "date": today},
                {"title": "Segunda", "body": "…", "url": "https://example.com/m",
                 "source": "Portafolio", "date": today},
            ]
        },
    )
    sent = _patch_send(monkeypatch)

    out = asyncio.run(research_reply({"query": "noticias de hoy"}))
    assert queries == ["news:noticias de hoy:d"]  # the vertical answered; no web fallback
    assert "2 result(s) sent" in out
    assert "El Tiempo" in sent[0] and today in sent[0]


def test_non_news_query_never_touches_the_news_vertical(monkeypatch):
    from lhassa.actionpack._research import research_reply

    queries = _patch_search(
        monkeypatch,
        {"python 3.14 release": [{"title": "t", "body": "b", "url": "https://e.com"}]},
    )
    _patch_send(monkeypatch)

    asyncio.run(research_reply({"query": "python 3.14 release"}))
    assert queries == ["python 3.14 release"]


def test_today_intent_filters_to_fresh_results():
    """"de hoy" means the SYSTEM date: stale items drop when fresh ones exist,
    relative stamps ('6 h') count as today, and order is freshest-first."""
    from datetime import datetime, timedelta, timezone

    from lhassa.actionpack._research import _freshest_first

    now = datetime(2026, 7, 6, 12, tzinfo=timezone.utc)
    old = (now - timedelta(days=18)).strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = [
        {"title": "vieja", "date": old},
        {"title": "ayer", "date": yesterday},
        {"title": "hace horas", "date": "Opinion6 h"},
        {"title": "sin fecha", "date": ""},
    ]
    fresh = _freshest_first(rows, today_only=True, now=now)
    assert [r["title"] for r in fresh] == ["hace horas", "ayer"]

    # Too few fresh → the honest full list, sorted freshest-first.
    rows2 = [{"title": "vieja", "date": old}, {"title": "hace horas", "date": "6 h"}]
    kept = _freshest_first(rows2, today_only=True, now=now)
    assert [r["title"] for r in kept] == ["hace horas", "vieja"]
