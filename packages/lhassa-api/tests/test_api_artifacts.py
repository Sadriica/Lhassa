"""Artifacts read-surface tests; the API router (``GET /api/artifacts``,
``GET /api/artifacts/{id}``) and the ``lhassa artifacts`` CLI sub-app.

API tests use a TestClient over a fresh app mounting just the router, the same
pattern as ``tests/test_flow_api.py``. CLI tests use Typer's ``CliRunner``
against the real ``app``, the same pattern as ``tests/test_flow_templates.py``.
Both surfaces are pure reads over :mod:`lhassa.core.artifacts` (covered on its
own in ``tests/test_artifacts.py``); artifacts are seeded here directly through
``core.artifacts.register``: the same seam :mod:`lhassa.org.deliver` uses;
never through HTTP/CLI (neither surface writes).
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from lhassa.api import artifacts_routes

runner = CliRunner()


def _use_tmp_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    return db


async def _seed(db, *, instance_id: str, count: int = 2):
    from lhassa.core import artifacts

    ids = []
    for i in range(count):
        artifact_id = await artifacts.register(
            instance_id,
            kind="cv",
            paths=[f"/tmp/cv-{i}.tex", f"/tmp/cv-{i}.pdf"],
            metadata={"company": f"Acme{i}", "role": "Backend Engineer"},
            origin_department="interns",
        )
        ids.append(artifact_id)
    return ids


@pytest.fixture
def client(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(artifacts_routes.router)
    return TestClient(app)


def test_list_empty(client):
    assert client.get("/api/artifacts").json() == []


def test_list_newest_first(client, tmp_path, monkeypatch):
    import lhassa.config as config_mod

    db = _use_tmp_db(tmp_path, monkeypatch)
    ids = asyncio.run(_seed(db, instance_id=config_mod.Config.instance_id))

    r = client.get("/api/artifacts")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 2
    # newest first: the second-registered artifact comes first.
    assert rows[0]["id"] == ids[-1]
    assert rows[0]["kind"] == "cv"
    assert rows[0]["origin_department"] == "interns"
    assert rows[0]["paths"][0].endswith(".tex")
    assert rows[0]["metadata"]["role"] == "Backend Engineer"
    assert "creado_en" in rows[0]


def test_list_respects_limit(client, tmp_path, monkeypatch):
    import lhassa.config as config_mod

    db = _use_tmp_db(tmp_path, monkeypatch)
    asyncio.run(_seed(db, instance_id=config_mod.Config.instance_id, count=3))

    r = client.get("/api/artifacts?limit=1")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_get_one(client, tmp_path, monkeypatch):
    import lhassa.config as config_mod

    db = _use_tmp_db(tmp_path, monkeypatch)
    ids = asyncio.run(_seed(db, instance_id=config_mod.Config.instance_id, count=1))

    r = client.get(f"/api/artifacts/{ids[0]}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == ids[0]


def test_get_missing_is_404(client):
    r = client.get("/api/artifacts/ghost")
    assert r.status_code == 404


def test_route_ordering_list_before_id(client, tmp_path, monkeypatch):
    # GET /api/artifacts (no id) must resolve to the list, not {artifact_id}.
    import lhassa.config as config_mod

    db = _use_tmp_db(tmp_path, monkeypatch)
    asyncio.run(_seed(db, instance_id=config_mod.Config.instance_id, count=1))
    r = client.get("/api/artifacts")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
