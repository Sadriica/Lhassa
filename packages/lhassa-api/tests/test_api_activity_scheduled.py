"""Offline API test for /api/scheduled (lhassa/api/activity_routes.py).

The route now reads pending ``send_reminder`` tasks from the ``tasks`` table
(``core/db.py``) instead of the legacy ``memory/store.py`` scheduled_tasks
table, reshaped into the response keys the dashboard already expects
(id/name/run_at/message/status/created_at). Uses httpx ASGITransport against
the FastAPI app (no live server), brain DB redirected to a tmp file.
"""

import httpx
import pytest
import pytest_asyncio  # noqa: F401


@pytest_asyncio.fixture
async def api(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.config import Config

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.api.server import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client, db, Config.instance_id


@pytest.mark.asyncio
async def test_scheduled_returns_minted_reminder(api):
    client, db, inst = api

    await db.add_task(
        inst,
        "send_reminder",
        objetivo="call John",
        params={"text": "Time to call John"},
        due_at="2026-04-01T14:20:00+00:00",
    )

    r = await client.get("/api/scheduled")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    row = body[0]
    assert row["name"] == "call John"
    assert row["run_at"] == "2026-04-01T14:20:00+00:00"
    assert row["message"] == "Time to call John"
    assert row["status"] == "pending"
    assert "created_at" in row


@pytest.mark.asyncio
async def test_scheduled_excludes_other_actions_and_maps_done_status(api):
    client, db, inst = api

    await db.add_task(inst, "notify", objetivo="not a reminder", params={"text": "x"})
    task_id = await db.add_task(
        inst, "send_reminder", objetivo="fired one", params={"text": "already ran"}
    )
    await db.set_task_state(inst, task_id, "hecha")

    r = await client.get("/api/scheduled")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["name"] == "fired one"
    assert body[0]["status"] == "done"


@pytest.mark.asyncio
async def test_scheduled_empty(api):
    client, _db, _inst = api

    r = await client.get("/api/scheduled")
    assert r.status_code == 200
    assert r.json() == []
