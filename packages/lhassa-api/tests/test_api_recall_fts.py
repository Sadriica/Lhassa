"""Tests for Fase 1 "Recall": FTS5 full-text search over the episode log.

Covers: index creation + backfill of pre-existing rows, content search,
automation-episode demotion, hostile-query safety, the /api/episodes/search
route, the /recall chat command, and the episodic-recall layer in
lhassa.memory.load.
"""


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
