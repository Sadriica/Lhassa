"""Tests for the typed-relationships API router (the dashboard overlay backend).

Offline: the topology and relationships files are redirected to tmp paths on the
live Config class attributes; the router is mounted on a throwaway FastAPI app and
driven with a TestClient. Asserts the happy paths, symmetry over HTTP, and the 4xx
rejections.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import lhassa.config as config_mod
    from lhassa.api.relationship_routes import router
    from lhassa.org import topology as topo
    from lhassa.org.topology import BrainNode, Topology

    topo_file = tmp_path / "org.json"
    rel_file = tmp_path / "relationships.json"
    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(topo_file))
    monkeypatch.setattr(config_mod.Config, "org_relationships_file", str(rel_file))
    monkeypatch.setattr(config_mod.Config, "org_role", "")
    monkeypatch.setattr(config_mod.Config, "org_departments", "")

    topo.save_topology(
        Topology(
            [
                BrainNode(id="general", role="general"),
                BrainNode(id="dept:a", role="department", department="a", parent="general"),
                BrainNode(id="dept:b", role="department", department="b", parent="general"),
            ]
        ),
        config_mod.Config(),
    )

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _pairs(body: dict) -> set:
    return {(e["from"], e["to"], e["kind"]) for e in body["relationships"]}


# ── reads ─────────────────────────────────────────────────────────────────────────


def test_get_empty_overlay(client):
    r = client.get("/api/org/relationships")
    assert r.status_code == 200
    body = r.json()
    assert body["relationships"] == []
    assert body["kinds"] == ["reads", "notifies", "reviews", "collaborates", "shares"]


def test_get_kinds_legend(client):
    r = client.get("/api/org/relationships/kinds")
    assert r.status_code == 200
    kinds = {k["kind"]: k for k in r.json()["kinds"]}
    assert set(kinds) == {"reads", "notifies", "reviews", "collaborates", "shares"}
    assert kinds["collaborates"]["symmetric"] is True
    assert kinds["reads"]["symmetric"] is False
    assert kinds["reads"]["description"]


# ── add / delete ──────────────────────────────────────────────────────────────────


def test_add_edge_returns_overlay(client):
    r = client.post(
        "/api/org/relationships",
        json={"from": "dept:a", "to": "dept:b", "kind": "reads", "note": "scope x"},
    )
    assert r.status_code == 200
    assert ("dept:a", "dept:b", "reads") in _pairs(r.json())


def test_collaborates_symmetry_over_http(client):
    client.post(
        "/api/org/relationships",
        json={"from": "dept:b", "to": "dept:a", "kind": "collaborates"},
    )
    # Reverse direction must dedupe to the same canonical edge.
    r = client.post(
        "/api/org/relationships",
        json={"from": "dept:a", "to": "dept:b", "kind": "collaborates"},
    )
    assert len(r.json()["relationships"]) == 1


def test_delete_edge_body(client):
    client.post(
        "/api/org/relationships",
        json={"from": "dept:a", "to": "dept:b", "kind": "notifies"},
    )
    r = client.request(
        "DELETE",
        "/api/org/relationships",
        json={"from": "dept:a", "to": "dept:b", "kind": "notifies"},
    )
    assert r.status_code == 200
    assert r.json()["relationships"] == []


def test_delete_edge_query_params(client):
    client.post(
        "/api/org/relationships",
        json={"from": "dept:a", "to": "dept:b", "kind": "reads"},
    )
    r = client.delete(
        "/api/org/relationships",
        params={"from": "dept:a", "to": "dept:b", "kind": "reads"},
    )
    assert r.status_code == 200
    assert r.json()["relationships"] == []


# ── 4xx rejections ────────────────────────────────────────────────────────────────


def test_self_edge_is_400(client):
    r = client.post(
        "/api/org/relationships",
        json={"from": "dept:a", "to": "dept:a", "kind": "reads"},
    )
    assert r.status_code == 400


def test_unknown_kind_is_400(client):
    r = client.post(
        "/api/org/relationships",
        json={"from": "dept:a", "to": "dept:b", "kind": "bogus"},
    )
    assert r.status_code == 400


def test_missing_brain_is_404(client):
    r = client.post(
        "/api/org/relationships",
        json={"from": "dept:a", "to": "dept:ghost", "kind": "reads"},
    )
    assert r.status_code == 404


def test_missing_fields_is_400(client):
    r = client.post("/api/org/relationships", json={"from": "dept:a", "kind": "reads"})
    assert r.status_code == 400


def test_file_round_trip_via_api(client, tmp_path):
    client.post(
        "/api/org/relationships",
        json={"from": "dept:a", "to": "dept:b", "kind": "reviews", "note": "gate"},
    )
    raw = json.loads((tmp_path / "relationships.json").read_text())
    assert raw == [
        {"from": "dept:a", "to": "dept:b", "kind": "reviews", "note": "gate", "scope": ""}
    ]
