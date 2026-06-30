"""Work-flow API tests — TestClient over a fresh app that mounts the router.

The router is self-contained; mounting it on a minimal app (rather than the full
server) keeps these tests independent of the integrator's wiring while exercising
exactly the same routes the integrator includes via
``app.include_router(flow_routes.router)``.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lhassa.api import flow_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)
    monkeypatch.setattr(config_mod.Config, "flows_file", str(tmp_path / "flows.json"))
    # A temp topology so a task node's auto-routing has something to route over.
    topo = tmp_path / "org.json"
    topo.write_text(
        '[{"id":"general","role":"general","department":null,"parent":null},'
        '{"id":"dept:optimization","role":"department","department":"optimization","parent":"general"}]'
    )
    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(topo))

    app = FastAPI()
    app.include_router(flow_routes.router)
    return TestClient(app)


def _sample(flow_id="f1"):
    return {
        "id": flow_id,
        "name": "demo",
        "description": "trigger→task→output",
        "nodes": [
            {"id": "t", "kind": "trigger", "config": {"type": "manual"}},
            {"id": "w", "kind": "task", "config": {"skill": "optimization", "prompt": "go"}},
            {"id": "o", "kind": "output", "config": {"type": "log", "target": "audit"}},
        ],
        "edges": [
            {"from_node": "t", "to_node": "w"},
            {"from_node": "w", "to_node": "o"},
        ],
    }


def test_crud_happy_path(client):
    # empty
    assert client.get("/api/flows").json() == []

    # create
    r = client.post("/api/flows", json=_sample())
    assert r.status_code == 201, r.text
    assert r.json()["id"] == "f1"

    # list + get
    assert {f["id"] for f in client.get("/api/flows").json()} == {"f1"}
    assert client.get("/api/flows/f1").json()["name"] == "demo"

    # patch a field
    r = client.patch("/api/flows/f1", json={"name": "renamed"})
    assert r.status_code == 200 and r.json()["name"] == "renamed"

    # put replaces
    replaced = _sample()
    replaced["description"] = "new desc"
    r = client.put("/api/flows/f1", json=replaced)
    assert r.status_code == 200 and r.json()["description"] == "new desc"

    # delete
    assert client.delete("/api/flows/f1").json() == {"deleted": "f1"}
    assert client.get("/api/flows/f1").status_code == 404


def test_create_bad_flow_is_400(client):
    bad = _sample()
    bad["nodes"] = [{"id": "x", "kind": "task", "config": {}}]  # no trigger
    r = client.post("/api/flows", json=bad)
    assert r.status_code == 400
    assert "trigger" in r.json()["detail"]


def test_create_unknown_kind_is_400(client):
    bad = _sample()
    bad["nodes"][1]["kind"] = "frobnicate"
    r = client.post("/api/flows", json=bad)
    assert r.status_code == 400


def test_duplicate_id_is_409(client):
    client.post("/api/flows", json=_sample())
    r = client.post("/api/flows", json=_sample())
    assert r.status_code == 409


def test_get_missing_is_404(client):
    assert client.get("/api/flows/ghost").status_code == 404
    assert client.delete("/api/flows/ghost").status_code == 404
    assert client.patch("/api/flows/ghost", json={"name": "x"}).status_code == 404


def test_run_endpoint_offline(client):
    client.post("/api/flows", json=_sample())
    r = client.post("/api/flows/f1/run", json={"inputs": {"summary": "seed"}})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["flow_id"] == "f1"
    assert rec["status"] == "completed"
    kinds = [s["kind"] for s in rec["steps"]]
    assert kinds == ["trigger", "task", "output"]
    # offline org stub ran (no live model)
    task = next(s for s in rec["steps"] if s["kind"] == "task")
    assert "[offline]" in task["result"]["summary"]


def test_run_missing_flow_is_404(client):
    assert client.post("/api/flows/ghost/run", json={}).status_code == 404
