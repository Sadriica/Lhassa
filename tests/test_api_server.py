"""Tests for potts.api.server REST and WebSocket behaviour."""

import json
from dataclasses import asdict

from fastapi.testclient import TestClient


def test_status_and_audit_endpoints(monkeypatch):
    from potts.api.server import app
    from potts.state import AuditEvent, state

    original_metrics = state.metrics.copy()
    original_audit = list(state.audit_events)
    original_voice = state.is_voice_active
    original_loaded = state._audit_loaded
    try:
        state.is_voice_active = True
        state._audit_loaded = True
        state.metrics = {
            "llm_calls": 1,
            "tool_calls": 2,
            "sessions": 3,
            "agents_launched": 4,
            "blocked_actions": 5,
        }
        state.audit_events = [
            AuditEvent(ts=1.0, action="shell", target="ls", source="voice_tool", status="ok", detail="exit_code=0")
        ]

        client = TestClient(app)
        status = client.get("/api/status")
        audit = client.get("/api/audit")

        assert status.status_code == 200
        assert status.json()["voice_active"] is True
        assert status.json()["metrics"]["blocked_actions"] == 5

        assert audit.status_code == 200
        assert audit.json() == [asdict(state.audit_events[0])]
    finally:
        state.metrics = original_metrics
        state.audit_events = original_audit
        state.is_voice_active = original_voice
        state._audit_loaded = original_loaded


def test_health_endpoint_reports_runtime_configuration(monkeypatch):
    import potts.api.server as server
    from potts.api.server import app
    from potts.state import state

    original_voice = state.is_voice_active
    original_agents = dict(state.agent_runs)
    original_ws = set(state._ws_clients)
    original_loaded = state._audit_loaded
    try:
        state.is_voice_active = True
        state._audit_loaded = True
        state.agent_runs = {"abc": object()}
        state._ws_clients = {object(), object()}

        monkeypatch.setenv("POTTS_LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("POTTS_AGENT_BACKEND", "builtin")
        monkeypatch.setenv("POTTS_VISION_PROVIDER", "openai")
        monkeypatch.setenv("POTTS_STT_LANGUAGE", "es")
        monkeypatch.setenv("POTTS_ALLOW_DESTRUCTIVE_SHELL", "1")
        monkeypatch.setenv("POTTS_FILE_WRITE_ROOT", "/tmp/potts-root")
        monkeypatch.setenv("POTTS_API_HOST", "127.0.0.1")
        monkeypatch.setenv("POTTS_API_PORT", "9999")

        import importlib
        import potts.config as config_mod

        importlib.reload(config_mod)
        monkeypatch.setattr(server, "Config", config_mod.Config)

        client = TestClient(app)
        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["voice_active"] is True
        assert data["agent_runs"] == 1
        assert data["ws_clients"] == 2
        assert data["llm_provider"] == "openai"
        assert data["agent_backend"] == "builtin"
        assert data["vision_provider"] == "openai"
        assert data["stt_language"] == "es"
        assert data["allow_destructive_shell"] is True
        assert data["file_write_root"] == "/tmp/potts-root"
        assert data["api_host"] == "127.0.0.1"
        assert data["api_port"] == 9999
    finally:
        state.is_voice_active = original_voice
        state.agent_runs = original_agents
        state._ws_clients = original_ws
        state._audit_loaded = original_loaded


def test_export_endpoint_returns_operational_snapshot(monkeypatch):
    import potts.api.server as server
    from potts.api.server import app
    from potts.state import AuditEvent, state

    original_metrics = state.metrics.copy()
    original_audit = list(state.audit_events)
    original_voice = state.is_voice_active
    original_loaded = state._audit_loaded
    try:
        state.is_voice_active = False
        state._audit_loaded = True
        state.metrics = {
            "llm_calls": 7,
            "tool_calls": 8,
            "sessions": 9,
            "agents_launched": 10,
            "blocked_actions": 2,
        }
        state.audit_events = [
            AuditEvent(ts=4.0, action="shell", target="pwd", source="agent", status="ok", detail="exit_code=0")
        ]

        async def fake_load_facts():
            return [{"key": "name", "value": "Alice", "updated": "2026-03-31T00:00:00+00:00"}]

        async def fake_load_recent_sessions(n):
            return [{"date": "2026-03-31T00:00:00+00:00", "summary": "Summary"}]

        async def fake_load_all_scheduled_tasks():
            return [{"id": 1, "name": "Task", "status": "pending"}]

        monkeypatch.setattr(server, "load_facts", fake_load_facts)
        monkeypatch.setattr(server, "load_recent_sessions", fake_load_recent_sessions)
        monkeypatch.setattr(server, "load_all_scheduled_tasks", fake_load_all_scheduled_tasks)

        client = TestClient(app)
        response = client.get("/api/export")

        assert response.status_code == 200
        data = response.json()
        assert "exported_at" in data
        assert data["metrics"]["blocked_actions"] == 2
        assert data["audit"][0]["target"] == "pwd"
        assert data["facts"][0]["key"] == "name"
        assert data["sessions"][0]["summary"] == "Summary"
        assert data["scheduled_tasks"][0]["name"] == "Task"
    finally:
        state.metrics = original_metrics
        state.audit_events = original_audit
        state.is_voice_active = original_voice
        state._audit_loaded = original_loaded


def test_import_endpoint_restores_audit_and_memory(monkeypatch):
    import potts.api.server as server
    from potts.api.server import app

    captured = {}

    async def fake_replace_memory_snapshot(*, facts, sessions, scheduled_tasks):
        captured["facts"] = facts
        captured["sessions"] = sessions
        captured["scheduled_tasks"] = scheduled_tasks

    audit_replaced = {}

    def fake_replace_audit_events(events):
        audit_replaced["events"] = events

    monkeypatch.setattr(server, "replace_memory_snapshot", fake_replace_memory_snapshot)
    monkeypatch.setattr(server.state, "replace_audit_events", fake_replace_audit_events)

    client = TestClient(app)
    response = client.post("/api/import", json={
        "audit": [{"ts": 1.0, "action": "shell", "target": "pwd", "source": "agent", "status": "ok", "detail": ""}],
        "facts": [{"key": "name", "value": "Alice"}],
        "sessions": [{"date": "2026-03-31T00:00:00+00:00", "summary": "Imported"}],
        "scheduled_tasks": [{"name": "Task", "run_at": "2099-01-01T00:00:00+00:00", "message": "Ping", "status": "pending"}],
    })

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert audit_replaced["events"][0]["target"] == "pwd"
    assert captured["facts"][0]["key"] == "name"
    assert captured["sessions"][0]["summary"] == "Imported"
    assert captured["scheduled_tasks"][0]["name"] == "Task"


def test_import_endpoint_rejects_invalid_payload():
    from potts.api.server import app

    client = TestClient(app)
    response = client.post("/api/import", json={"audit": {}, "facts": [], "sessions": [], "scheduled_tasks": []})

    assert response.status_code == 400
    assert "must be arrays" in response.json()["detail"]


def test_import_endpoint_dry_run_validates_without_persisting(monkeypatch):
    import potts.api.server as server
    from potts.api.server import app

    calls = {"replace_memory_snapshot": 0, "replace_audit_events": 0}

    async def fake_replace_memory_snapshot(*, facts, sessions, scheduled_tasks):
        calls["replace_memory_snapshot"] += 1

    def fake_replace_audit_events(events):
        calls["replace_audit_events"] += 1

    monkeypatch.setattr(server, "replace_memory_snapshot", fake_replace_memory_snapshot)
    monkeypatch.setattr(server.state, "replace_audit_events", fake_replace_audit_events)

    client = TestClient(app)
    response = client.post("/api/import?dry_run=1", json={
        "audit": [{"ts": 1.0, "action": "shell", "target": "pwd", "source": "agent", "status": "ok", "detail": ""}],
        "facts": [{"key": "name", "value": "Alice"}],
        "sessions": [{"date": "2026-03-31T00:00:00+00:00", "summary": "Imported"}],
        "scheduled_tasks": [{"name": "Task", "run_at": "2099-01-01T00:00:00+00:00", "message": "Ping", "status": "pending"}],
    })

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert response.json()["facts"] == 1
    assert calls == {"replace_memory_snapshot": 0, "replace_audit_events": 0}


def test_websocket_snapshot_and_audit_event(monkeypatch):
    from potts.api.server import app
    from potts.state import AuditEvent, state

    original_metrics = state.metrics.copy()
    original_audit = list(state.audit_events)
    original_voice = state.is_voice_active
    original_loaded = state._audit_loaded
    try:
        state.is_voice_active = False
        state._audit_loaded = True
        state.metrics = {
            "llm_calls": 0,
            "tool_calls": 0,
            "sessions": 0,
            "agents_launched": 0,
            "blocked_actions": 0,
        }
        state.audit_events = [
            AuditEvent(ts=2.0, action="write_file", target="/tmp/x", source="agent", status="blocked", detail="blocked")
        ]

        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            snapshot = json.loads(ws.receive_text())
            assert snapshot["type"] == "snapshot"
            assert snapshot["audit"] == [asdict(state.audit_events[0])]
    finally:
        state.metrics = original_metrics
        state.audit_events = original_audit
        state.is_voice_active = original_voice
        state._audit_loaded = original_loaded
