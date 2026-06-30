"""Tests for the additive org-management API endpoints (the dashboard backend).

Offline: the topology file is redirected to a tmp path via the live Config class
attribute and the brain DB to a tmp file, mirroring tests/test_org_cmds.py. A
FastAPI TestClient drives the routes like tests/test_api_server.py. These tests
assert the happy paths, a bad-body 4xx, and that an EXISTING route is unaffected.
"""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import lhassa.config as config_mod
    import lhassa.core.db as db
    from lhassa.api.server import app

    topo_file = tmp_path / "org.json"
    topo_file.write_text(json.dumps([
        {"id": "general", "role": "general", "department": None, "parent": None},
        {"id": "dept:optimizacion", "role": "department",
         "department": "optimizacion", "parent": "general"},
        {"id": "dept:finanzas", "role": "department",
         "department": "finanzas", "parent": "general"},
    ]))
    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(topo_file))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    return TestClient(app)


def _ids(topology: dict) -> set:
    return {n["id"] for n in topology["nodes"]}


# ── reads ───────────────────────────────────────────────────────────────────────


def test_get_topology(client):
    r = client.get("/api/org/topology")
    assert r.status_code == 200
    body = r.json()
    # Lhassa (the permanent administrator) is injected at the root on load.
    assert body["root"] == "lhassa"
    assert body["admin"] == "lhassa"
    assert _ids(body) == {"lhassa", "general", "dept:optimizacion", "dept:finanzas"}
    lhassa = next(n for n in body["nodes"] if n["id"] == "lhassa")
    assert lhassa["is_admin"] is True


def test_get_skills(client):
    r = client.get("/api/org/skills")
    assert r.status_code == 200
    assert "optimizacion" in {s["domain"] for s in r.json()}


def test_get_route_preview(client):
    r = client.get("/api/org/route", params={"goal": "optimize the slow latency"})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == ["lhassa", "general", "dept:optimizacion"]
    assert body["leaf"] == "dept:optimizacion"


# ── create / update / delete ────────────────────────────────────────────────────


def test_create_brain(client):
    r = client.post("/api/org/brains", json={
        "id": "dept:doc", "role": "department", "parent": "general",
        "department": "documentacion", "skills": ["optimizacion"],
        "limits": {"budget_usd": 1.0},
    })
    assert r.status_code == 200
    assert "dept:doc" in _ids(r.json())
    doc = next(n for n in r.json()["nodes"] if n["id"] == "dept:doc")
    assert doc["skills"] == ["optimizacion"]
    assert doc["limits"] == {"budget_usd": 1.0}


def test_create_brain_bad_parent_is_4xx(client):
    r = client.post("/api/org/brains", json={"id": "dept:z", "parent": "ghost"})
    assert r.status_code == 400
    # the chart is untouched — the bad op never persisted.
    assert "dept:z" not in _ids(client.get("/api/org/topology").json())


def test_create_brain_missing_id_is_400(client):
    r = client.post("/api/org/brains", json={"role": "department"})
    assert r.status_code == 400


def test_patch_brain(client):
    r = client.patch("/api/org/brains/dept:finanzas", json={
        "skills": ["finanzas", "budgets"], "limits": {"max_risk": "low"},
    })
    assert r.status_code == 200
    fin = next(n for n in r.json()["nodes"] if n["id"] == "dept:finanzas")
    assert fin["skills"] == ["finanzas", "budgets"]
    assert fin["limits"] == {"max_risk": "low"}


def test_patch_missing_brain_is_404(client):
    r = client.patch("/api/org/brains/nope", json={"role": "lead"})
    assert r.status_code == 404


def test_move_brain(client):
    r = client.post("/api/org/brains/dept:finanzas/move",
                    json={"parent": "dept:optimizacion"})
    assert r.status_code == 200
    fin = next(n for n in r.json()["nodes"] if n["id"] == "dept:finanzas")
    assert fin["parent"] == "dept:optimizacion"


def test_move_brain_missing_parent_is_400(client):
    r = client.post("/api/org/brains/dept:finanzas/move", json={})
    assert r.status_code == 400


def test_promote_brain(client):
    r = client.post("/api/org/brains/dept:optimizacion/promote")
    assert r.status_code == 200
    body = r.json()
    # Lhassa stays the permanent root; the promoted brain becomes a direct child.
    assert body["root"] == "lhassa"
    opt = next(n for n in body["nodes"] if n["id"] == "dept:optimizacion")
    assert opt["parent"] == "lhassa"


def test_delete_admin_is_400(client):
    r = client.delete("/api/org/brains/lhassa")
    assert r.status_code == 400
    # the administrator is still there.
    assert "lhassa" in _ids(client.get("/api/org/topology").json())


def test_move_admin_is_400(client):
    r = client.post("/api/org/brains/lhassa/move", json={"parent": "general"})
    assert r.status_code == 400


def test_delete_leaf(client):
    r = client.delete("/api/org/brains/dept:finanzas")
    assert r.status_code == 200
    assert "dept:finanzas" not in _ids(r.json())


def test_delete_with_children_is_400_without_force(client):
    r = client.delete("/api/org/brains/general")
    assert r.status_code == 400


def test_delete_subtree_with_force(client):
    # re-parent finanzas under optimizacion, then force-delete optimizacion's subtree
    client.post("/api/org/brains/dept:finanzas/move", json={"parent": "dept:optimizacion"})
    r = client.delete("/api/org/brains/dept:optimizacion", params={"force": "true"})
    assert r.status_code == 200
    assert _ids(r.json()) == {"lhassa", "general"}


# ── existing routes unaffected ────────────────────────────────────────────────────


def test_existing_status_route_still_works(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    assert "metrics" in r.json()
