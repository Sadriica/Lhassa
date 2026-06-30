"""Tests for the /api/overview aggregate (the dashboard-home digest).

Fully offline: the brain DB is redirected to a tmp file, the gateway ledger to a
tmp file, and the org topology to a tmp JSON file. The aggregate is built
defensively, so we also assert a degraded subsystem is reported (not fatal).
"""

import asyncio
import importlib
import json

from fastapi.testclient import TestClient


def _setup(tmp_path, monkeypatch):
    """Point the brain DB, gateway ledger and topology file at tmp; reload config."""
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")

    from lhassa.llm import gateway as gw_mod

    monkeypatch.setattr(gw_mod.gateway, "db_path", tmp_path / "gw.db")
    monkeypatch.setattr(gw_mod.gateway, "_initialized", False)

    topo = tmp_path / "org.json"
    topo.write_text(
        json.dumps(
            [
                {"id": "lhassa", "role": "general", "parent": None},
                {"id": "research", "role": "department", "parent": "lhassa"},
            ]
        )
    )
    monkeypatch.setenv("LHASSA_ORG_TOPOLOGY_FILE", str(topo))

    import lhassa.config as config_mod

    importlib.reload(config_mod)

    import lhassa.api.server as server

    monkeypatch.setattr(server, "Config", config_mod.Config)
    return server, db, config_mod.Config


def _seed(db, inst):
    async def _go():
        await db.init_db()
        await db.upsert_tool(
            inst, "speedup", dominio="optimization", estado="cuarentena"
        )
        await db.add_approval(
            inst,
            "send_email",
            {"accion": "send_email", "actor_id": "duenio", "origen_trust": 2},
        )
        from lhassa.core.episode import Episode

        ep = Episode.begin(despertar_por="heartbeat", instance_id=inst)
        ep.finish(decision="rest", accion="noop", resultado="all quiet")
        await ep.append()

    asyncio.run(_go())


def test_overview_returns_all_sections(tmp_path, monkeypatch):
    server, db, Config = _setup(tmp_path, monkeypatch)
    # Keep health fast + off the real home: a fixed digest, no systemctl/git shell-out.
    from lhassa.health import checks

    monkeypatch.setattr(
        checks, "run_all", lambda *a, **k: {"worst_level": "info", "ok": True}
    )
    _seed(db, Config.instance_id)

    client = TestClient(server.app)
    res = client.get("/api/overview")
    assert res.status_code == 200
    data = res.json()

    # Every section is present.
    for key in ("brains", "skills", "approvals", "cost", "health", "activity", "tools"):
        assert key in data
    assert data["errors"] == {}

    # Brains come from the temp topology (admin + one department).
    assert data["brains"]["count"] == 2
    assert data["brains"]["admin"] == "lhassa"
    assert data["brains"]["standalone"] is False

    # Approvals carry the pending list (so the owner can approve from the overview).
    assert data["approvals"]["pending"] == 1
    item = data["approvals"]["items"][0]
    assert item["tipo"] == "send_email"
    assert item["summary"] == "send_email"

    # Cost mirrors the /api/llm/limits path.
    assert data["cost"]["kill_switch"] is False
    assert "daily_token_budget" in data["cost"]
    assert "today_cost_usd" in data["cost"]

    # Health + activity + tools.
    assert data["health"]["ok"] is True
    assert any(ep["despertar_por"] == "heartbeat" for ep in data["activity"]["recent"])
    assert data["tools"]["count"] == 1
    assert data["tools"]["by_estado"]["cuarentena"] == 1

    # Skills counts are integers (read defensively from the store/catalog).
    assert isinstance(data["skills"]["installed"], int)
    assert isinstance(data["skills"]["recommended"], int)


def test_overview_degraded_subsystem_is_reported_not_fatal(tmp_path, monkeypatch):
    server, db, Config = _setup(tmp_path, monkeypatch)
    _seed(db, Config.instance_id)

    # Force the health subsystem to blow up — it must be reported, not fatal.
    from lhassa.health import checks

    def _boom(*a, **k):
        raise RuntimeError("sensors offline")

    monkeypatch.setattr(checks, "run_all", _boom)

    client = TestClient(server.app)
    res = client.get("/api/overview")
    assert res.status_code == 200  # never 500
    data = res.json()

    assert "health" in data["errors"]
    assert "sensors offline" in data["errors"]["health"]
    # The degraded section falls back to a safe default…
    assert data["health"] == {"worst_level": "info", "ok": True}
    # …and every other section is still there.
    assert data["brains"]["count"] == 2
    assert data["approvals"]["pending"] == 1
