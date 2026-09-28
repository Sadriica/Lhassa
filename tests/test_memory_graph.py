"""Tests for lhassa.memory.graph; the deterministic $0 knowledge graph.

Fully offline: the brain DB is redirected to a tmp file per test, seeded with a
few tagged facts and episodes mentioning "Sezzle" and "Ana", then queried
through the graph.py primitives, the API routes, and the CLI.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import pytest_asyncio  # noqa: F401
from typer.testing import CliRunner

from lhassa.config import Config

INST = Config.instance_id
runner = CliRunner()


async def _seed(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.core.episode import Episode
    from lhassa.memory import facts

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    await facts.add_fact(
        INST, "dato", "Owes a Sezzle installment due next week.",
        tags=["Sezzle", "finance"], procedencia="test",
    )
    await facts.add_fact(
        INST, "dato", "Ana is the owner's sister and lives in Lima.",
        tags=["Ana", "family"], procedencia="test",
    )
    await facts.add_fact(
        INST, "dato", "Prefers dark roast coffee.",
        tags=["prefs"], procedencia="test",
    )

    ep1 = Episode.begin(despertar_por="chat", instance_id=INST)
    ep1.finish(decision="think", accion="reply", resultado="Reminded owner about the Sezzle payment.")
    await db.append_episode(ep1)

    ep2 = Episode.begin(despertar_por="chat", instance_id=INST)
    ep2.finish(decision="think", accion="reply", resultado="Talked about Ana's trip to Lima.")
    await db.append_episode(ep2)

    return db


# ── what_do_i_know ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_what_do_i_know_surfaces_facts_episodes_and_related_terms(tmp_path, monkeypatch):
    await _seed(tmp_path, monkeypatch)
    from lhassa.memory import graph

    result = await graph.what_do_i_know(INST, "sezzle")
    assert result["query"] == "sezzle"
    assert any("Sezzle" in f["contenido"] for f in result["facts"])
    assert any("Sezzle" in ep.get("resultado", "") for ep in result["episodes"])
    assert result["first_seen"] is not None
    assert result["last_seen"] is not None
    assert result["first_seen"] <= result["last_seen"]
    # Coffee has nothing to do with sezzle.
    assert not any("coffee" in f["contenido"].lower() for f in result["facts"])


@pytest.mark.asyncio
async def test_what_do_i_know_related_terms_for_ana(tmp_path, monkeypatch):
    await _seed(tmp_path, monkeypatch)
    from lhassa.memory import graph

    result = await graph.what_do_i_know(INST, "ana")
    assert any("Ana" in f["contenido"] or "sister" in f["contenido"] for f in result["facts"])
    # "Lima" co-occurs with Ana in both the fact and the episode.
    assert "Lima" in result["related_terms"]


@pytest.mark.asyncio
async def test_what_do_i_know_empty_query_degrades(tmp_path, monkeypatch):
    await _seed(tmp_path, monkeypatch)
    from lhassa.memory import graph

    result = await graph.what_do_i_know(INST, "")
    assert result == {
        "query": "",
        "facts": [],
        "episodes": [],
        "related_terms": [],
        "first_seen": None,
        "last_seen": None,
    }


@pytest.mark.asyncio
async def test_what_do_i_know_unknown_term_returns_empty_lists(tmp_path, monkeypatch):
    await _seed(tmp_path, monkeypatch)
    from lhassa.memory import graph

    result = await graph.what_do_i_know(INST, "zzznonexistentzzz")
    assert result["facts"] == []
    assert result["episodes"] == []


@pytest.mark.asyncio
async def test_what_do_i_know_never_raises_on_bad_db(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "does_not_exist" / "brain.db")
    from lhassa.memory import graph

    # No init_db() called; the parent dir doesn't even exist. Must degrade, not raise.
    result = await graph.what_do_i_know(INST, "sezzle")
    assert result["facts"] == []
    assert result["episodes"] == []


# ── entities ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entities_lists_tagged_terms_with_counts(tmp_path, monkeypatch):
    await _seed(tmp_path, monkeypatch)
    from lhassa.memory import graph

    rows = await graph.entities(INST)
    terms = {r["term"] for r in rows}
    assert "Sezzle" in terms
    assert "Ana" in terms
    for r in rows:
        if r["term"] == "Sezzle":
            assert r["mentions"] >= 1
            assert r["last_seen"]


@pytest.mark.asyncio
async def test_entities_empty_db_returns_empty_list(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    from lhassa.memory import graph

    rows = await graph.entities(INST)
    assert rows == []


# ── timeline ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_orders_by_time(tmp_path, monkeypatch):
    await _seed(tmp_path, monkeypatch)
    from lhassa.memory import graph

    items = await graph.timeline(INST, "sezzle")
    assert items, "expected at least one timeline entry"
    times = [it["creado_en"] for it in items]
    assert times == sorted(times)
    assert any(it["kind"] == "fact" for it in items)


@pytest.mark.asyncio
async def test_timeline_empty_entity_degrades(tmp_path, monkeypatch):
    await _seed(tmp_path, monkeypatch)
    from lhassa.memory import graph

    assert await graph.timeline(INST, "") == []
    assert await graph.timeline(INST, "totally-unknown-xyz") == []


# ── API ───────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def api(tmp_path, monkeypatch):
    await _seed(tmp_path, monkeypatch)
    from lhassa.api.server import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


@pytest.mark.asyncio
async def test_api_memory_graph(api):
    r = await api.get("/api/memory/graph", params={"q": "sezzle"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "sezzle"
    assert any("Sezzle" in f["contenido"] for f in body["facts"])


@pytest.mark.asyncio
async def test_api_memory_graph_empty_query(api):
    r = await api.get("/api/memory/graph")
    assert r.status_code == 200
    body = r.json()
    assert body["facts"] == []
    assert body["episodes"] == []


@pytest.mark.asyncio
async def test_api_memory_entities(api):
    r = await api.get("/api/memory/entities")
    assert r.status_code == 200
    body = r.json()
    terms = {e["term"] for e in body["entities"]}
    assert "Sezzle" in terms


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_memory_graph_renders(tmp_path, monkeypatch):
    asyncio.run(_seed(tmp_path, monkeypatch))
    from lhassa.cli.app import app

    res = runner.invoke(app, ["memory", "graph", "sezzle"])
    assert res.exit_code == 0, res.output
    assert "Sezzle" in res.output


def test_cli_memory_graph_json(tmp_path, monkeypatch):
    asyncio.run(_seed(tmp_path, monkeypatch))
    from lhassa.cli.app import app

    res = runner.invoke(app, ["memory", "graph", "sezzle", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["query"] == "sezzle"


def test_cli_memory_graph_empty_query_does_not_crash(tmp_path, monkeypatch):
    asyncio.run(_seed(tmp_path, monkeypatch))
    from lhassa.cli.app import app

    res = runner.invoke(app, ["memory", "graph", "zzznonexistentzzz"])
    assert res.exit_code == 0, res.output
    assert "Nothing known" in res.output


def test_cli_memory_entities_renders(tmp_path, monkeypatch):
    asyncio.run(_seed(tmp_path, monkeypatch))
    from lhassa.cli.app import app

    res = runner.invoke(app, ["memory", "entities"])
    assert res.exit_code == 0, res.output
    assert "Sezzle" in res.output


def test_cli_memory_entities_json_empty(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    from lhassa.cli.app import app

    res = runner.invoke(app, ["memory", "entities", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == []
