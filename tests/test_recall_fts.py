"""Tests for Fase 1 "Recall": FTS5 full-text search over the episode log.

Covers: index creation + backfill of pre-existing rows, content search,
automation-episode demotion, hostile-query safety, the /api/episodes/search
route, the /recall chat command, and the episodic-recall layer in
lhassa.memory.load.
"""

import asyncio

import httpx
import pytest
import pytest_asyncio  # noqa: F401


async def _init_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


async def _make_episode(db, instance_id, *, despertar_por, decision="d", resultado="r"):
    from lhassa.core.episode import Episode

    ep = Episode.begin(despertar_por=despertar_por, instance_id=instance_id)
    ep.finish(decision=decision, accion="a", resultado=resultado)
    await ep.append()
    return ep.id


# ── index creation + backfill ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_indexes_pre_existing_episodes(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa._vendor import aiosqlite

    db_path = tmp_path / "brain.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    # Simulate an "old" DB: only the episodes table exists (no FTS index yet),
    # with rows already in it; as if upgrading from a pre-Fase-1 install.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE episodes (
                id               TEXT PRIMARY KEY,
                instance_id      TEXT NOT NULL,
                creado_en        TEXT NOT NULL,
                despertar_por    TEXT,
                decision         TEXT,
                accion           TEXT,
                resultado        TEXT,
                dominio          TEXT,
                costo_usd        REAL,
                memoria_cargada  TEXT,
                raw              TEXT
            )
        """)
        await conn.execute(
            """INSERT INTO episodes(
                   id, instance_id, creado_en, despertar_por, decision, accion,
                   resultado, dominio, costo_usd, memoria_cargada, raw)
               VALUES('ep_pre1', 'inst_personal', '2026-01-01T00:00:00+00:00',
                      'telegram', 'sync', 'a', 'unicornio pastel de queso',
                      NULL, 0.0, '[]', '{}')"""
        )
        await conn.commit()

    # Now run init_db() as if upgrading; it must create the FTS index AND
    # backfill the pre-existing row (rebuild), never crash.
    await db.init_db()

    results = await db.search_episodes("inst_personal", "unicornio", 10)
    assert len(results) == 1
    assert results[0]["id"] == "ep_pre1"


@pytest.mark.asyncio
async def test_new_episodes_are_indexed_via_triggers(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    await _make_episode(
        db, "inst_personal", despertar_por="telegram", resultado="mango de temporada"
    )
    results = await db.search_episodes("inst_personal", "mango", 10)
    assert len(results) == 1
    assert "mango" in results[0]["resultado"].lower()


# ── content search ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_finds_by_content_word(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    await _make_episode(
        db, "inst_personal", despertar_por="telegram", resultado="revisó el repositorio zorglub"
    )
    await _make_episode(
        db, "inst_personal", despertar_por="timer", resultado="nada que hacer"
    )
    results = await db.search_episodes("inst_personal", "zorglub", 10)
    assert len(results) == 1
    assert results[0]["despertar_por"] == "telegram"


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    await _make_episode(db, "inst_personal", despertar_por="telegram", resultado="algo")
    assert await db.search_episodes("inst_personal", "", 10) == []
    assert await db.search_episodes("inst_personal", "   ", 10) == []


@pytest.mark.asyncio
async def test_search_scoped_to_instance(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    await _make_episode(db, "inst_a", despertar_por="telegram", resultado="tornasol brillante")
    await _make_episode(db, "inst_b", despertar_por="telegram", resultado="tornasol brillante")
    results = await db.search_episodes("inst_a", "tornasol", 10)
    assert len(results) == 1


# ── automation demotion ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_automation_episodes_are_demoted_not_hidden(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    await _make_episode(
        db, "inst_personal", despertar_por="monitor", resultado="quantumfizzle detectado"
    )
    await _make_episode(
        db, "inst_personal", despertar_por="telegram", resultado="quantumfizzle mencionado"
    )
    await _make_episode(
        db, "inst_personal", despertar_por="task:sync_repo", resultado="quantumfizzle sincronizado"
    )

    results = await db.search_episodes("inst_personal", "quantumfizzle", 10)
    assert len(results) == 3  # demoted, not hidden

    despertar_order = [r["despertar_por"] for r in results]
    telegram_idx = despertar_order.index("telegram")
    monitor_idx = despertar_order.index("monitor")
    task_idx = despertar_order.index("task:sync_repo")
    assert telegram_idx < monitor_idx
    assert telegram_idx < task_idx


@pytest.mark.asyncio
async def test_org_transport_hops_are_demoted_below_interactive(tmp_path, monkeypatch):
    """The hierarchy's own paperwork must not outrank a real conversation.

    org-deliver/org-delegate were the single largest source of episodes (227 of
    309 in one measured week) and used to compete on equal footing, so the
    brain's memory of its own life filled with its own memos.
    """
    db = await _init_db(tmp_path, monkeypatch)
    await _make_episode(
        db, "inst_personal", despertar_por="org-deliver", resultado="quantumfizzle entregado"
    )
    await _make_episode(
        db, "inst_personal", despertar_por="org-delegate", resultado="quantumfizzle delegado"
    )
    await _make_episode(
        db, "inst_personal", despertar_por="telegram", resultado="quantumfizzle preguntado"
    )

    results = await db.search_episodes("inst_personal", "quantumfizzle", 10)
    assert len(results) == 3  # demoted, not hidden

    order = [r["despertar_por"] for r in results]
    assert order.index("telegram") < order.index("org-deliver")
    assert order.index("telegram") < order.index("org-delegate")


@pytest.mark.asyncio
async def test_org_execution_is_demoted_more_gently_than_transport(tmp_path, monkeypatch):
    """temple-execute holds the real resultado, so it must outrank pure transport.

    "What happened with curate_skills?" has to find the hop that actually ran it,
    not the memo that says it was forwarded.
    """
    db = await _init_db(tmp_path, monkeypatch)
    await _make_episode(
        db, "inst_personal", despertar_por="org-deliver", resultado="quantumfizzle reenviado"
    )
    await _make_episode(
        db, "inst_personal", despertar_por="temple-execute", resultado="quantumfizzle archivado"
    )

    results = await db.search_episodes("inst_personal", "quantumfizzle", 10)
    order = [r["despertar_por"] for r in results]
    assert order.index("temple-execute") < order.index("org-deliver")


@pytest.mark.asyncio
async def test_a_strong_automation_match_still_beats_a_weak_interactive_one(
    tmp_path, monkeypatch
):
    """The penalty is a nudge, not a filter; relevance can still win.

    This needs a REALISTIC corpus to be meaningful. bm25 is scored against
    corpus statistics: with only two documents its IDF term collapses to ~0, so
    the flat penalty dominates and the demotion behaves like a filter. Once
    there is a real body of episodes the scores spread well past the penalty and
    a strong automation match outranks a weak interactive one, as intended.
    """
    db = await _init_db(tmp_path, monkeypatch)
    for n in range(200):
        await _make_episode(
            db,
            "inst_personal",
            despertar_por="org-deliver",
            resultado=f"entrega rutinaria numero {n} sin relacion alguna",
        )
    await _make_episode(
        db, "inst_personal", despertar_por="temple-execute",
        resultado="quantumfizzle archivado",  # short + on-topic → strong match
    )
    await _make_episode(
        db, "inst_personal", despertar_por="telegram",
        resultado="quantumfizzle " + "relleno " * 300,  # diluted → weak match
    )

    results = await db.search_episodes("inst_personal", "quantumfizzle", 10)
    assert len(results) == 2
    assert results[0]["despertar_por"] == "temple-execute"


# ── hostile query strings ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostile_query",
    [
        '"a" OR (b',
        "x AND NOT",
        "((()))",
        'foo" bar',
        "NEAR(a b",
        "*",
        '"',
    ],
)
async def test_hostile_queries_never_raise(tmp_path, monkeypatch, hostile_query):
    db = await _init_db(tmp_path, monkeypatch)
    await _make_episode(db, "inst_personal", despertar_por="telegram", resultado="algo normal")
    # Must not raise; a bad user query degrades to (possibly empty) results.
    results = await db.search_episodes("inst_personal", hostile_query, 10)
    assert isinstance(results, list)


# ── /api/episodes/search ───────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def api_client(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.config import Config

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.api.server import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client, db, Config.instance_id


@pytest.mark.asyncio
async def test_api_episodes_search(api_client):
    client, db, inst = api_client
    await _make_episode(db, inst, despertar_por="telegram", resultado="jirafa multicolor")

    r = await client.get("/api/episodes/search", params={"q": "jirafa"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "jirafa"
    assert body["count"] == 1
    assert len(body["results"]) == 1
    assert "jirafa" in body["results"][0]["resultado"].lower()


@pytest.mark.asyncio
async def test_api_episodes_search_validates_n(api_client):
    client, _db, _inst = api_client
    r = await client.get("/api/episodes/search", params={"q": "x", "n": 0})
    assert r.status_code == 422
    r2 = await client.get("/api/episodes/search", params={"q": "x", "n": 201})
    assert r2.status_code == 422


# ── /recall command ────────────────────────────────────────────────────────────


def _cfg(instance_id="inst_personal"):
    from types import SimpleNamespace

    return SimpleNamespace(instance_id=instance_id)


def test_recall_no_args_returns_usage_hint():
    from lhassa.notify import commands

    reply = asyncio.run(commands.dispatch("/recall", config=_cfg()))
    assert "Usage" in reply


def test_recall_finds_match(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    from lhassa.core import db as _db
    from lhassa.notify import commands

    asyncio.run(
        _make_episode(
            _db, "inst_personal", despertar_por="telegram", resultado="platano espacial raro"
        )
    )
    reply = asyncio.run(commands.dispatch("/recall platano", config=_cfg()))
    assert "platano" in reply.lower() or "espacial" in reply.lower()
    assert "telegram" in reply


def test_recall_no_match_reports_clearly(tmp_path, monkeypatch):
    asyncio.run(_init_db(tmp_path, monkeypatch))
    from lhassa.notify import commands

    reply = asyncio.run(commands.dispatch("/recall nonexistentxyz123", config=_cfg()))
    assert "No episodes match" in reply


def test_help_lists_recall():
    from lhassa.notify import commands

    reply = asyncio.run(commands.dispatch("/help", config=_cfg()))
    assert "/recall" in reply


# ── memory/load.py episodic recall layer ───────────────────────────────────────


@pytest.mark.asyncio
async def test_load_includes_recall_layer_when_query_matches(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    from lhassa.memory import load

    await _make_episode(
        db, "inst_personal", despertar_por="telegram", resultado="capibara filosofo insomne"
    )
    ctx = await load.load(
        "inst_personal", token_budget=10_000, query="capibara filosofo insomne"
    )
    assert len(ctx.recall_episodes) >= 1
    assert "Recall" in ctx.to_prompt()


@pytest.mark.asyncio
async def test_load_recall_layer_empty_without_query(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    from lhassa.memory import load

    await _make_episode(
        db, "inst_personal", despertar_por="telegram", resultado="capibara filosofo insomne"
    )
    ctx = await load.load("inst_personal", token_budget=10_000)
    assert ctx.recall_episodes == []


@pytest.mark.asyncio
async def test_load_recall_truncates_before_higher_priority_layers(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    from lhassa.memory import load, seed

    await seed.seed_default_facts("inst_personal")
    # Plenty of recent episodes (layer "recent") plus a distinct recall match.
    for i in range(5):
        await _make_episode(
            db, "inst_personal", despertar_por="timer", resultado=f"episodio de relleno numero {i}"
        )
    await _make_episode(
        db, "inst_personal", despertar_por="telegram", resultado="ornitorrinco cuantico bailarin"
    )

    # A tiny budget: the mandatory identity core must survive, but the lowest
    # priority (recall) layer must be the first one dropped.
    ctx = await load.load(
        "inst_personal",
        token_budget=1,
        recent_n=20,
        query="ornitorrinco cuantico bailarin",
    )
    assert ctx.recall_episodes == []
    # Identity core (directives) always present regardless of budget.
    assert ctx.directives


@pytest.mark.asyncio
async def test_load_recall_layer_defensive_on_error(tmp_path, monkeypatch):
    """Any exception from the search must yield an empty layer, never raise."""
    db = await _init_db(tmp_path, monkeypatch)
    from lhassa.memory import load

    async def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "search_episodes", _boom)
    ctx = await load.load("inst_personal", token_budget=10_000, query="whatever")
    assert ctx.recall_episodes == []
