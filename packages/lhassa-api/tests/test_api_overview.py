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

    # The AppState singleton is process-wide; isolate the run-derived sections.
    from lhassa.state import state

    state.agent_runs.clear()

    client = TestClient(server.app)
    res = client.get("/api/overview")
    assert res.status_code == 200
    data = res.json()

    # Every section is present.
    for key in (
        "brains", "skills", "approvals", "cost", "health", "activity", "tools",
        "personal",
    ):
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
    assert item["summary"] == "Run 'send_email'"
    assert "asked by duenio" in item["detail"]

    # Cost mirrors the /api/llm/limits path.
    assert data["cost"]["kill_switch"] is False
    assert "daily_token_budget" in data["cost"]
    assert "today_cost_usd" in data["cost"]

    # Health + activity + tools.
    assert data["health"]["ok"] is True
    assert any(ep["despertar_por"] == "heartbeat" for ep in data["activity"]["recent"])
    assert data["activity"]["total"] == 1
    assert data["activity"]["last_24h"] == 1
    assert data["activity"]["recent_limit"] == 8
    assert data["activity"]["window"] == "24h"
    assert data["tools"]["count"] == 1
    assert data["tools"]["by_estado"]["cuarentena"] == 1

    # Skills counts are integers (read defensively from the store/catalog).
    assert isinstance(data["skills"]["installed"], int)
    assert isinstance(data["skills"]["recommended"], int)

    # Command-center sections are always present with the right shape.
    assert isinstance(data["now_running"], list)  # nothing running in a fresh state
    assert data["now_running"] == []
    assert set(data["pulse"]) >= {"last_beat", "mode", "alive"}
    assert data["pulse"]["mode"] in ("online", "offline")
    assert "next_tick" not in data["pulse"]  # never fabricated
    assert isinstance(data["flows"]["count"], int)
    assert data["flows"]["last_status"] is None  # flow runs aren't persisted yet
    assert set(data["runs_today"]) == {"done", "stopped", "failed"}
    assert "priorities" in data["personal"]
    assert data["personal"]["priorities"]
    assert "Resolve approval" in data["personal"]["priorities"][0]


def test_overview_command_center_reflects_agent_runs(tmp_path, monkeypatch):
    """now_running lists the in-flight runs; runs_today buckets terminal statuses."""
    import time as _time

    server, db, Config = _setup(tmp_path, monkeypatch)
    from lhassa.health import checks

    monkeypatch.setattr(
        checks, "run_all", lambda *a, **k: {"worst_level": "info", "ok": True}
    )
    _seed(db, Config.instance_id)

    from lhassa.state import AgentRun, state

    state.agent_runs.clear()
    now = _time.time()
    state.agent_runs["r1"] = AgentRun(
        id="r1", name="ai-cli (claude)", status="running", task="do a thing",
        cli="claude", started_at=now,
    )
    state.agent_runs["r2"] = AgentRun(
        id="r2", name="ai-cli (codex)", status="done", task="older", cli="codex",
        started_at=now, finished_at=now,
    )
    state.agent_runs["r3"] = AgentRun(
        id="r3", name="ai-cli (opencode)", status="stopped", task="halted",
        cli="opencode", started_at=now, finished_at=now,
    )
    try:
        client = TestClient(server.app)
        data = client.get("/api/overview").json()

        ids = [r["id"] for r in data["now_running"]]
        assert ids == ["r1"]  # only the running one
        assert data["now_running"][0]["cli"] == "claude"

        assert data["runs_today"]["done"] == 1
        assert data["runs_today"]["stopped"] == 1
        assert data["runs_today"]["failed"] == 0

        # last_beat comes from the seeded episode; mode is offline (ORG_LIVE unset).
        assert data["pulse"]["last_beat"] is not None
        assert data["pulse"]["mode"] == "offline"
        assert data["pulse"]["alive"] is True
    finally:
        state.agent_runs.clear()


def test_overview_degraded_subsystem_is_reported_not_fatal(tmp_path, monkeypatch):
    server, db, Config = _setup(tmp_path, monkeypatch)
    _seed(db, Config.instance_id)

    # Force the health subsystem to blow up; it must be reported, not fatal.
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
