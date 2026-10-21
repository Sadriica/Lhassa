"""Tests for the additive config + org-template API endpoints (dashboard backend).

Offline: HOME is redirected so settings.env writes under a temp ~/.lhassa, the
topology file + brain DB to a tmp path, and editable Config attrs are snapshotted so
a PATCH that mutates the live Config class is restored at teardown. A FastAPI
TestClient drives the routes; an existing route is asserted unaffected.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import lhassa.config as config_mod
    import lhassa.core.db as db
    from lhassa import settings
    from lhassa.api.server import app

    monkeypatch.setenv("HOME", str(tmp_path))
    topo_file = tmp_path / "org.json"
    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(topo_file))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    # Snapshot editable Config attrs so a live PATCH is restored after the test.
    for entry in settings.EDITABLE:
        monkeypatch.setattr(
            config_mod.Config, entry["attr"], getattr(config_mod.Config, entry["attr"])
        )
    return TestClient(app)


# ── /api/config ─────────────────────────────────────────────────────────────────


def test_get_config(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert {"settings", "groups", "available"} <= set(body)
    keys = {s["key"] for s in body["settings"]}
    assert "llm_kill_switch" in keys


def test_patch_config_single_key(client):
    r = client.patch("/api/config", json={"key": "router_mode", "value": "off"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["applied"] is True
    row = next(s for s in body["effective"]["settings"] if s["key"] == "router_mode")
    assert row["value"] == "off" and row["overridden"] is True
    # Persisted to settings.env under the temp HOME.
    from pathlib import Path

    assert "LHASSA_ROUTER=off" in (Path.home() / ".lhassa" / "settings.env").read_text()


def test_patch_config_updates_map(client):
    r = client.patch(
        "/api/config",
        json={"updates": {"voice_enabled": "1", "notify_channels": "desktop"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert all(res["applied"] for res in body["results"])
    assert "restart" in body["restart_hint"].lower() or "no restart" in body["restart_hint"].lower()


def test_patch_config_bad_key_is_400(client):
    r = client.patch("/api/config", json={"key": "nope", "value": "x"})
    assert r.status_code == 400


def test_patch_config_bad_enum_is_400(client):
    r = client.patch("/api/config", json={"key": "heartbeat_provider", "value": "bogus"})
    assert r.status_code == 400


# ── /api/org/templates ──────────────────────────────────────────────────────────


def test_get_org_templates(client):
    r = client.get("/api/org/templates")
    assert r.status_code == 200
    ids = {t["id"] for t in r.json()}
    assert "software-factory" in ids


def test_apply_org_template(client):
    r = client.post("/api/org/templates/software-factory/apply")
    assert r.status_code == 200
    body = r.json()
    ids = {n["id"] for n in body["chart"]["nodes"]}
    assert "software-factory:delivery" in ids and "software-factory:frontend" in ids
    assert body["chart"]["root"] == "lhassa"


def test_apply_bad_template_is_4xx(client):
    r = client.post("/api/org/templates/nope/apply")
    assert r.status_code == 400


# ── existing route unaffected ────────────────────────────────────────────────────


def test_existing_org_topology_route_unaffected(client):
    r = client.get("/api/org/topology")
    assert r.status_code == 200
    assert r.json()["root"] == "lhassa"
