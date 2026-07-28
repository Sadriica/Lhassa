"""Project API tests — TestClient over a fresh app that mounts just the
project router, mirroring tests/test_flow_api.py's style (fresh app, tmp DB).
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lhassa.api import project_routes
from lhassa.core import db, projects

INSTANCE = "inst_personal"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(config_mod.Config, "instance_id", INSTANCE)
    asyncio.run(db.init_db())

    app = FastAPI()
    app.include_router(project_routes.router)
    return TestClient(app)


def _create(name="lhassa", **kw):
    return asyncio.run(projects.create_project(INSTANCE, name, **kw))


def test_list_active_and_all(client):
    assert client.get("/api/projects").json() == []

    active = _create("alpha", kind="code")
    archived = _create("beta", kind="research")
    asyncio.run(projects.archive_project(INSTANCE, archived["id"]))

    only_active = client.get("/api/projects").json()
    assert [p["id"] for p in only_active] == [active["id"]]

    everything = client.get("/api/projects?all=1").json()
    assert {p["id"] for p in everything} == {active["id"], archived["id"]}

    by_kind = client.get("/api/projects?kind=research&all=1").json()
    assert [p["id"] for p in by_kind] == [archived["id"]]


def test_get_project_ok_and_404(client):
    row = _create("gamma")
    got = client.get(f"/api/projects/{row['id']}")
    assert got.status_code == 200
    assert got.json()["name"] == "gamma"

    assert client.get("/api/projects/ghost").status_code == 404


def test_runs_scoped_by_entity_tag_and_404_on_unknown_project(client):
    row = _create("delta")
    other = _create("epsilon")

    asyncio.run(
        db.record_run(
            {
                "instance_id": INSTANCE,
                "kind": "flow",
                "entity_id": projects.entity_tag(row["id"]),
                "status": "completed",
                "summary": "for delta",
            }
        )
    )
    asyncio.run(
        db.record_run(
            {
                "instance_id": INSTANCE,
                "kind": "flow",
                "entity_id": projects.entity_tag(other["id"]),
                "status": "completed",
                "summary": "for epsilon",
            }
        )
    )
    # A run with an unrelated entity_id must never leak in.
    asyncio.run(
        db.record_run(
            {
                "instance_id": INSTANCE,
                "kind": "flow",
                "entity_id": "not-a-project-tag",
                "status": "completed",
            }
        )
    )

    r = client.get(f"/api/projects/{row['id']}/runs")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 1
    assert runs[0]["summary"] == "for delta"

    assert client.get("/api/projects/ghost/runs").status_code == 404


def test_artifacts_filtered_by_metadata_project_id(client):
    row = _create("zeta")
    other = _create("eta")

    asyncio.run(
        db.insert_artifact(
            {
                "id": "art-1",
                "instance_id": INSTANCE,
                "kind": "document",
                "paths": ["/tmp/a.pdf"],
                "metadata": {"project_id": row["id"]},
                "creado_en": db._now(),
            }
        )
    )
    asyncio.run(
        db.insert_artifact(
            {
                "id": "art-2",
                "instance_id": INSTANCE,
                "kind": "document",
                "paths": ["/tmp/b.pdf"],
                "metadata": {"project_id": other["id"]},
                "creado_en": db._now(),
            }
        )
    )
    asyncio.run(
        db.insert_artifact(
            {
                "id": "art-3",
                "instance_id": INSTANCE,
                "kind": "document",
                "paths": ["/tmp/c.pdf"],
                "metadata": {},
                "creado_en": db._now(),
            }
        )
    )

    r = client.get(f"/api/projects/{row['id']}/artifacts")
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()]
    assert ids == ["art-1"]

    assert client.get("/api/projects/ghost/artifacts").status_code == 404


def test_episodes_404_on_unknown_project_and_ok_on_known(client):
    row = _create("theta")
    assert client.get("/api/projects/ghost/episodes").status_code == 404
    r = client.get(f"/api/projects/{row['id']}/episodes")
    assert r.status_code == 200
    assert r.json() == []
