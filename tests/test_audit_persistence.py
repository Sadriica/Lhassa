"""Tests for persistent audit storage."""

import json

import pytest


def test_load_audit_events_restores_state(tmp_path):
    from potts.state import AppState

    app_state = AppState()
    app_state._AUDIT_PATH = tmp_path / "audit.json"
    app_state._AUDIT_PATH.write_text(json.dumps([
        {
            "ts": 1.0,
            "action": "shell",
            "target": "pwd",
            "source": "voice_tool",
            "status": "blocked",
            "detail": "blocked",
        },
        {
            "ts": 2.0,
            "action": "write_file",
            "target": "/tmp/x",
            "source": "agent",
            "status": "ok",
            "detail": "bytes=3",
        },
    ]))

    app_state.load_audit_events()

    assert len(app_state.audit_events) == 2
    assert app_state.audit_events[0].action == "shell"
    assert app_state.metrics["blocked_actions"] == 1


def test_load_audit_events_ignores_corrupt_file(tmp_path):
    from potts.state import AppState

    app_state = AppState()
    app_state._AUDIT_PATH = tmp_path / "audit.json"
    app_state._AUDIT_PATH.write_text("{not-json")

    app_state.load_audit_events()

    assert app_state.audit_events == []


@pytest.mark.asyncio
async def test_record_audit_event_persists_limit(tmp_path):
    from potts.state import AppState

    app_state = AppState()
    app_state._AUDIT_PATH = tmp_path / "audit.json"

    async def fake_broadcast(_event: dict):
        return None

    app_state.broadcast = fake_broadcast  # type: ignore[method-assign]

    for i in range(105):
        await app_state.record_audit_event(
            action="shell",
            target=f"cmd-{i}",
            source="agent",
            status="ok",
            detail="",
        )

    saved = json.loads(app_state._AUDIT_PATH.read_text())
    assert len(saved) == 100
    assert saved[0]["target"] == "cmd-104"
    assert saved[-1]["target"] == "cmd-5"
