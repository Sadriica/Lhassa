"""Work-flow API tests — TestClient over a fresh app that mounts the router.

The router is self-contained; mounting it on a minimal app (rather than the full
server) keeps these tests independent of the integrator's wiring while exercising
exactly the same routes the integrator includes via
``app.include_router(flow_routes.router)``.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lhassa.api import approval_routes, flow_routes, flow_run_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    import lhassa.config as config_mod
    import lhassa.core.db as db

    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)
    monkeypatch.setattr(config_mod.Config, "flows_file", str(tmp_path / "flows.json"))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    # A temp topology so a task node's auto-routing has something to route over.
    topo = tmp_path / "org.json"
    topo.write_text(
        '[{"id":"general","role":"general","department":null,"parent":null},'
        '{"id":"dept:optimization","role":"department","department":"optimization","parent":"general"}]'
    )
    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(topo))

    app = FastAPI()
    app.include_router(flow_routes.router)
    app.include_router(flow_run_routes.router)
    app.include_router(approval_routes.router)
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


def _approval_sample(flow_id="approval-flow"):
    flow = _sample(flow_id)
    flow["nodes"].insert(
        2,
        {"id": "gate", "kind": "gate", "config": {"type": "approval"}},
    )
    flow["edges"] = [
        {"from_node": "t", "to_node": "w"},
        {"from_node": "w", "to_node": "gate"},
        {"from_node": "gate", "to_node": "o"},
    ]
    return flow


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
    import asyncio
    import lhassa.config as config_mod
    import lhassa.core.db as db

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
    assert rec["id"]
    assert rec["duration_ms"] >= 0
    assert all("duration_ms" in s for s in rec["steps"])

    runs = asyncio.run(db.list_runs(config_mod.Config.instance_id, kind="flow", entity_id="f1"))
    assert len(runs) == 1
    assert runs[0]["id"] == rec["id"]
    steps = asyncio.run(db.list_run_steps(config_mod.Config.instance_id, rec["id"]))
    assert [s["kind"] for s in steps] == ["trigger", "task", "output"]
    assert steps[1]["entity_id"] == "optimization"


def test_run_missing_flow_is_404(client):
    assert client.post("/api/flows/ghost/run", json={}).status_code == 404


def test_approval_gate_pauses_and_resumes_from_checkpoint(client):
    client.post("/api/flows", json=_approval_sample())
    paused = client.post(
        "/api/flows/approval-flow/run",
        json={"inputs": {"summary": "draft"}},
    )
    assert paused.status_code == 200, paused.text
    waiting = paused.json()
    assert waiting["status"] == "waiting_approval"
    assert waiting["finished_at"] is None
    assert [step["kind"] for step in waiting["steps"]] == [
        "trigger",
        "task",
        "gate",
    ]

    approvals = client.get("/api/approvals").json()
    assert len(approvals) == 1
    approval_id = approvals[0]["id"]
    assert approvals[0]["payload"]["run_id"] == waiting["id"]
    assert approvals[0]["payload"]["node_id"] == "gate"

    operational = client.get(f"/api/flow-runs/{waiting['id']}")
    assert operational.status_code == 200
    assert operational.json()["status"] == "waiting_approval"

    resolved = client.post(
        f"/api/approvals/{approval_id}",
        json={"approved": True, "by_actor": "owner"},
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["run_id"] == waiting["id"]
    assert body["run_status"] == "completed"
    assert [step["kind"] for step in body["run"]["steps"]] == [
        "trigger",
        "task",
        "gate",
        "output",
    ]
    assert sum(step["kind"] == "task" for step in body["run"]["steps"]) == 1

    import asyncio
    import lhassa.config as config_mod
    import lhassa.core.db as db

    projected = asyncio.run(db.list_run_steps(config_mod.Config.instance_id, waiting["id"]))
    assert [step["kind"] for step in projected] == [
        "trigger",
        "task",
        "gate",
        "output",
    ]
    assert projected[2]["status"] == "completed"

    events = client.get(f"/api/flow-runs/{waiting['id']}/events").json()
    kinds = [event["kind"] for event in events]
    assert "APPROVAL_REQUESTED" in kinds
    assert "APPROVAL_RESOLVED" in kinds
    assert kinds[-1] == "RUN_COMPLETED"
    assert client.post(
        f"/api/approvals/{approval_id}", json={"approved": True}
    ).status_code == 409


def test_rejected_flow_gate_cancels_without_downstream_output(client):
    client.post("/api/flows", json=_approval_sample("rejected-flow"))
    waiting = client.post("/api/flows/rejected-flow/run", json={}).json()
    approval_id = client.get("/api/approvals").json()[0]["id"]

    resolved = client.post(
        f"/api/approvals/{approval_id}", json={"approved": False}
    )

    assert resolved.status_code == 200, resolved.text
    record = resolved.json()["run"]
    assert record["status"] == "cancelled"
    assert not any(step["kind"] == "output" for step in record["steps"])
    assert client.get(f"/api/flow-runs/{waiting['id']}").json()["status"] == "cancelled"


# ── flow-templates gallery ────────────────────────────────────────────────────────


def test_list_flow_templates(client):
    r = client.get("/api/flows/templates")
    assert r.status_code == 200, r.text
    ids = {t["id"] for t in r.json()}
    assert {"research-brief", "code-review-pass", "daily-summary", "content-pipeline"} <= ids


def test_flow_templates_route_ordering_not_captured_as_flow_id(client):
    # /api/flows/templates must resolve to the gallery, NOT get_flow(flow_id="templates").
    r = client.get("/api/flows/templates")
    assert r.status_code == 200
    assert isinstance(r.json(), list)  # get_flow would 404 (a list is never a 404 body)


def test_apply_flow_template_creates_a_flow(client):
    r = client.post("/api/flows/templates/daily-summary/apply")
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["id"] == "daily-summary"
    assert created["name"] == "Daily Summary"

    # It is now a real stored flow, reachable through the normal /{flow_id} route.
    got = client.get("/api/flows/daily-summary")
    assert got.status_code == 200
    assert got.json()["name"] == "Daily Summary"


def test_apply_flow_template_reapply_gets_a_fresh_id(client):
    first = client.post("/api/flows/templates/daily-summary/apply").json()
    second = client.post("/api/flows/templates/daily-summary/apply").json()
    assert first["id"] == "daily-summary"
    assert second["id"] == "daily-summary-2"


def test_apply_unknown_flow_template_is_400(client):
    r = client.post("/api/flows/templates/nope/apply")
    assert r.status_code == 400
