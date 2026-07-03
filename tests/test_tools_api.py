"""Light, offline API test for the quarantine room (GET /api/tools).

Uses httpx ASGITransport against the FastAPI app (no live server), the brain DB
redirected to a tmp file, and no GitHub/network.
"""

import httpx
import pytest
import pytest_asyncio  # noqa: F401


@pytest_asyncio.fixture
async def api(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.config import Config

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(Config, "tools_repo_dir", str(tmp_path / "tools"))
    monkeypatch.setattr(Config, "github_token", "")
    await db.init_db()

    from lhassa.api.server import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client, db, Config.instance_id


@pytest.mark.asyncio
async def test_get_tools_returns_rows(api):
    client, db, inst = api
    await db.upsert_tool(
        inst,
        "speedup",
        dominio="optimization",
        estado="cuarentena",
        metrica_baseline=10.0,
        metrica_actual=6.0,
    )
    r = await client.get("/api/tools")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    row = next(t for t in rows if t["nombre"] == "speedup")
    assert row["dominio"] == "optimization"
    assert row["estado"] == "cuarentena"
    assert row["metrica_baseline"] == 10.0
    assert row["metrica_actual"] == 6.0
    assert row["usos"] == 0
    assert row["fallos"] == 0


@pytest.mark.asyncio
async def test_get_tools_empty(api):
    client, _db, _inst = api
    r = await client.get("/api/tools")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_graduate_endpoint_marks_graduada_and_resolves_approval(api):
    client, db, inst = api
    # The tool has already passed the gates (agents.gates.promote records this
    # by setting estado="graduada"); the endpoint is the human-approval + merge
    # step, not the gate check itself.
    await db.upsert_tool(inst, "speedup", dominio="optimization", estado="graduada")

    r = await client.post("/api/tools/speedup/graduate", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["estado"] == "graduada"

    # Tool advanced to graduada.
    row = await db.get_tool(inst, "speedup")
    assert row["estado"] == "graduada"

    # A tool_graduation approval was created and resolved (queue is the seam).
    from lhassa._vendor import aiosqlite

    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT estado FROM approvals WHERE tipo = 'tool_graduation'"
        ) as cur:
            rows = [dict(x) for x in await cur.fetchall()]
    assert rows and rows[0]["estado"] == "aprobada"


@pytest.mark.asyncio
async def test_graduate_endpoint_refuses_ungated_tool(api):
    """A tool that has not passed the gates (estado != 'graduada') is refused."""
    client, db, inst = api
    await db.upsert_tool(inst, "half_baked", dominio="optimization", estado="cuarentena")

    r = await client.post("/api/tools/half_baked/graduate", json={})
    assert r.status_code == 409

    # Nothing changed: still cuarentena, no approval was created.
    row = await db.get_tool(inst, "half_baked")
    assert row["estado"] == "cuarentena"

    from lhassa._vendor import aiosqlite

    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT estado FROM approvals WHERE tipo = 'tool_graduation'"
        ) as cur:
            rows = [dict(x) for x in await cur.fetchall()]
    assert rows == []


@pytest.mark.asyncio
async def test_graduate_endpoint_refuses_unknown_tool(api):
    """A tool with no row at all is refused, not silently created/graduated."""
    client, _db, _inst = api
    r = await client.post("/api/tools/never-existed/graduate", json={})
    assert r.status_code == 409
