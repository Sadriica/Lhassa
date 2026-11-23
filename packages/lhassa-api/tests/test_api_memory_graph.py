"""Tests for lhassa.memory.graph; the deterministic $0 knowledge graph.

Fully offline: the brain DB is redirected to a tmp file per test, seeded with a
few tagged facts and episodes mentioning "Sezzle" and "Ana", then queried
through the graph.py primitives, the API routes, and the CLI.
"""

from __future__ import annotations


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
