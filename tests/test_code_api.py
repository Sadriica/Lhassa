"""Tests for the agentic-code launch API (lhassa.api.code_routes).

The router is mounted on a throwaway FastAPI app and driven via TestClient. No real
CLI is ever spawned: ``make_agent`` is monkeypatched to a fake agent whose
``_invoke`` returns canned output, and ``is_installed`` / ``available_clis`` are
patched so the tests don't depend on what's installed on the box.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lhassa.agents import local_cli
from lhassa.api import code_routes
from lhassa.state import state


class _FakeAgent:
    """Stand-in for LocalCliAgent — never spawns a subprocess."""

    def __init__(self, cli: str = "claude"):
        self.cli = cli
        self.invoked = False

    async def _invoke(self, run_id, run, task, cwd):
        self.invoked = True
        run.output = "fake output"
        await asyncio.sleep(0)
        return "fake output"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(code_routes.router)
    return TestClient(app)


def test_list_clis(client, monkeypatch):
    monkeypatch.setattr(local_cli, "available_clis", lambda: ["claude", "codex"])
    monkeypatch.setattr(local_cli, "detect_ai_cli", lambda: "claude")
    resp = client.get("/api/code/clis")
    assert resp.status_code == 200
    assert resp.json() == {"clis": ["claude", "codex"], "default": "claude"}


def test_launch_returns_run_id(client, monkeypatch):
    monkeypatch.setattr(local_cli, "is_installed", lambda cli: True)
    monkeypatch.setattr(local_cli, "available_clis", lambda: ["claude"])
    monkeypatch.setattr(local_cli, "make_agent", lambda cli: _FakeAgent(cli))

    resp = client.post("/api/code", json={"task": "fix the bug", "cli": "claude"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["cli"] == "claude"
    run_id = body["run_id"]
    assert isinstance(run_id, str) and run_id
    # The run was registered synchronously so the panel can correlate WS events.
    assert run_id in state.agent_runs
    assert state.agent_runs[run_id].task == "fix the bug"


def test_launch_auto_detects_cli(client, monkeypatch):
    monkeypatch.setattr(local_cli, "detect_ai_cli", lambda: "codex")
    monkeypatch.setattr(local_cli, "is_installed", lambda cli: True)
    monkeypatch.setattr(local_cli, "available_clis", lambda: ["codex"])
    monkeypatch.setattr(local_cli, "make_agent", lambda cli: _FakeAgent(cli))

    resp = client.post("/api/code", json={"task": "do a thing"})
    assert resp.status_code == 200
    assert resp.json()["cli"] == "codex"


def test_empty_task_is_400(client, monkeypatch):
    monkeypatch.setattr(local_cli, "is_installed", lambda cli: True)
    monkeypatch.setattr(local_cli, "make_agent", lambda cli: _FakeAgent(cli))
    resp = client.post("/api/code", json={"task": "   ", "cli": "claude"})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_missing_task_is_400(client):
    resp = client.post("/api/code", json={"cli": "claude"})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_uninstalled_cli_is_400(client, monkeypatch):
    monkeypatch.setattr(local_cli, "is_installed", lambda cli: False)
    monkeypatch.setattr(local_cli, "available_clis", lambda: [])
    resp = client.post("/api/code", json={"task": "build it", "cli": "codex"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert "codex" in body["error"]


def test_runs_endpoint_shape(client, monkeypatch):
    monkeypatch.setattr(local_cli, "is_installed", lambda cli: True)
    monkeypatch.setattr(local_cli, "available_clis", lambda: ["claude"])
    monkeypatch.setattr(local_cli, "make_agent", lambda cli: _FakeAgent(cli))
    launched = client.post("/api/code", json={"task": "list me", "cli": "claude"})
    run_id = launched.json()["run_id"]

    resp = client.get("/api/code/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert isinstance(runs, list)
    match = [r for r in runs if r["id"] == run_id]
    assert match and set(match[0]) >= {"id", "name", "status", "task", "output"}
