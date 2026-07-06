"""Offline API tests for the Phase-6 reports / projections / fleet endpoints.

Uses httpx ASGITransport against the FastAPI app (no live server), the brain DB
and the gateway ledger both redirected to tmp files, and ccusage stubbed.
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

    # Gateway ledger to a tmp file (projections reads it).
    from lhassa.llm import gateway as gw_mod
    monkeypatch.setattr(gw_mod.gateway, "db_path", tmp_path / "gw.db")
    monkeypatch.setattr(gw_mod.gateway, "_initialized", False)

    async def fake_ccusage(*a, **k):
        return {"available": False, "by_harness": {}, "total_cost_usd": 0.0}

    import lhassa.llm.ccusage as ccusage_mod
    monkeypatch.setattr(ccusage_mod, "read_ccusage", fake_ccusage)

    from lhassa.api.server import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client, db, Config.instance_id


@pytest.mark.asyncio
async def test_reports_summary(api):
    client, db, inst = api
    from lhassa.core.episode import Episode

    ep = Episode.begin(despertar_por="checkin:laptop", instance_id=inst)
    ep.finish(decision="checkin", accion="checkin", resultado="ok")
    await ep.append()
    ep2 = Episode.begin(despertar_por="timer", instance_id=inst)
    ep2.finish(decision="step", accion="noop", resultado="nada que hacer")
    await ep2.append()

    r = await client.get("/api/reports")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["by_despertar"]["checkin"] == 1
    assert body["by_despertar"]["timer"] == 1
    assert body["by_decision"]["step"] == 1
    assert len(body["recent"]) == 2

    all_rows = (await client.get("/api/reports?n=0")).json()
    assert all_rows["count"] == 2
    assert all_rows["total"] == 2
    assert all_rows["limit"] == 0


@pytest.mark.asyncio
async def test_projections_shape(api):
    client, _db, _inst = api
    r = await client.get("/api/projections")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"today", "budget", "projection", "ccusage"}
    assert body["today"]["cost_usd"] == 0.0
    assert "projected_month_cost_usd" in body["projection"]
    assert body["ccusage"]["available"] is False


@pytest.mark.asyncio
async def test_analytics_flow_and_brain_summaries(api):
    client, db, inst = api

    await db.record_run({
        "id": "run1",
        "instance_id": inst,
        "kind": "flow",
        "entity_id": "flow-a",
        "status": "completed",
        "trigger": "manual",
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:02+00:00",
        "duration_ms": 2000,
        "cost_usd": 0.03,
    })
    await db.record_run_step({
        "instance_id": inst,
        "run_id": "run1",
        "idx": 0,
        "kind": "task",
        "entity_id": "optimization",
        "status": "completed",
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:02+00:00",
        "duration_ms": 2000,
        "cost_usd": 0.03,
    })

    summary = (await client.get("/api/analytics/summary?range=all")).json()
    assert summary["total"]["runs"] == 1
    assert summary["by_kind"]["flow"]["total_cost_usd"] == 0.03

    flows = (await client.get("/api/analytics/flows?range=all")).json()
    flow = next(item for item in flows["items"] if item["entity_id"] == "flow-a")
    assert flow["runs"] == 1
    assert flow["avg_duration_ms"] == 2000

    brains = (await client.get("/api/analytics/brains?range=all")).json()
    brain = next(item for item in brains["items"] if item["entity_id"] == "optimization")
    assert brain["runs"] == 1
    assert brain["success_rate"] == 1.0


@pytest.mark.asyncio
async def test_fleet_lists_machines(api):
    client, db, inst = api
    from lhassa.core.episode import Episode

    await db.upsert_desired_state(inst, "laptop", "repoA", "main")
    ep = Episode.begin(despertar_por="checkin:laptop", instance_id=inst)
    ep.finish(decision="checkin", accion="checkin", resultado="ok")
    await ep.append()

    r = await client.get("/api/fleet")
    assert r.status_code == 200
    fleet = r.json()
    laptop = next(m for m in fleet if m["machine_id"] == "laptop")
    assert laptop["desired_state"] == {"repoA": "main"}
    assert laptop["last_checkin"]


@pytest.mark.asyncio
async def test_fleet_empty(api):
    client, _db, _inst = api
    r = await client.get("/api/fleet")
    assert r.status_code == 200
    assert r.json() == []
