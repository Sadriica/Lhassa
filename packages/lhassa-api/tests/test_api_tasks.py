"""Phase 4 tests; standing goals, watchlist senses, task routes, CLI parsing.

All tests are FULLY OFFLINE: no network calls, no real provider.
Migrations are tested against a temp DB created with the OLD schema then migrated.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest_asyncio  # noqa: F401


# ── helpers ────────────────────────────────────────────────────────────────────


def _iid() -> str:
    return "inst_test"


def _setup_db(tmp_path, monkeypatch, db_name="api_test.db"):
    """Sync helper: redirect DB_PATH, init schema, return the db module."""
    import lhassa.core.db as db_mod

    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / db_name)
    asyncio.run(db_mod.init_db())
    return db_mod


def test_api_task_list(tmp_path, monkeypatch):
    """GET /api/tasks returns tasks list."""
    db_mod = _setup_db(tmp_path, monkeypatch)
    asyncio.run(db_mod.add_task("inst_personal", "sync_repo", objetivo="/tmp/r"))

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["accion"] == "sync_repo"


def test_api_task_create_known_accion(tmp_path, monkeypatch):
    """POST /api/tasks with a registered accion returns {id, estado}."""
    from lhassa.core import actions as _actions

    _setup_db(tmp_path, monkeypatch, "api_create.db")

    # Ensure sync_repo is registered.
    assert "sync_repo" in _actions.ACTIONS

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post(
        "/api/tasks",
        json={"accion": "sync_repo", "objetivo": "/tmp/r"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert "estado" in data


def test_api_task_unknown_accion_rejected(tmp_path, monkeypatch):
    """POST /api/tasks with an unknown accion without judgment=True → 400."""
    _setup_db(tmp_path, monkeypatch, "api_reject.db")

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post("/api/tasks", json={"accion": "totally_unknown_xyz"})
    assert resp.status_code == 400
    assert "totally_unknown_xyz" in resp.json().get("detail", "")


def test_api_task_judgment_flag_allows_any_accion(tmp_path, monkeypatch):
    """POST /api/tasks with judgment=True allows any accion → 201."""
    _setup_db(tmp_path, monkeypatch, "api_judgment.db")

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post(
        "/api/tasks",
        json={"accion": "review my inbox", "judgment": True},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data


def test_api_task_create_with_recurrence(tmp_path, monkeypatch):
    """POST /api/tasks with recurrence='daily' mints a recurring task."""
    db_mod = _setup_db(tmp_path, monkeypatch, "api_recurrence.db")

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post(
        "/api/tasks",
        json={"accion": "sync_repo", "objetivo": "/tmp/r", "recurrence": "daily"},
    )
    assert resp.status_code == 201

    # Verify recurrence was persisted.
    tasks = asyncio.run(db_mod.list_tasks("inst_personal", limit=10))
    task = next((t for t in tasks if t.get("accion") == "sync_repo"), None)
    assert task is not None
    assert task.get("recurrence") == "86400"


def test_api_watchlist_preview(tmp_path, monkeypatch):
    """GET /api/watchlist returns {senses, preview} without side effects."""
    _setup_db(tmp_path, monkeypatch, "api_watchlist.db")

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/watchlist")
    assert resp.status_code == 200
    data = resp.json()
    assert "senses" in data
    assert "preview" in data
    assert isinstance(data["senses"], list)
    assert "_from_pending_tasks" in data["senses"]
    assert "_from_finished_jobs" in data["senses"]
    assert "_from_repo_changes" in data["senses"]
    assert "_from_health" in data["senses"]
    assert isinstance(data["preview"], list)


def test_api_tasks_filter_by_estado(tmp_path, monkeypatch):
    """GET /api/tasks?estado=hecha returns only completed tasks."""
    db_mod = _setup_db(tmp_path, monkeypatch, "api_filter.db")

    tid = asyncio.run(db_mod.add_task("inst_personal", "sync_repo"))
    asyncio.run(db_mod.set_task_state("inst_personal", tid, "hecha"))
    asyncio.run(db_mod.add_task("inst_personal", "sync_repo"))  # pendiente

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/tasks?estado=hecha")
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["estado"] == "hecha" for r in data)
    assert len(data) == 1


def test_api_parse_recurrence_aliases():
    """task_routes._parse_recurrence translates string aliases."""
    from lhassa.api.task_routes import _parse_recurrence

    assert _parse_recurrence("hourly") == "3600"
    assert _parse_recurrence("daily") == "86400"
    assert _parse_recurrence("weekly") == "604800"
    assert _parse_recurrence(3600) == "3600"
    assert _parse_recurrence("7200") == "7200"
    assert _parse_recurrence(None) is None


def test_api_parse_due_at_relative():
    """task_routes._parse_due_at handles +Nh shorthand."""
    from lhassa.api.task_routes import _parse_due_at

    now = datetime.now(timezone.utc)
    result = _parse_due_at("+2h")
    dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
    delta = (dt - now).total_seconds()
    assert 7100 < delta < 7300


def test_api_parse_due_at_iso():
    """task_routes._parse_due_at passes through valid ISO 8601."""
    from lhassa.api.task_routes import _parse_due_at

    iso = "2035-06-01T12:00:00+00:00"
    assert _parse_due_at(iso) == iso
    assert _parse_due_at(None) is None
