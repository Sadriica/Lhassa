"""Tests for potts.state."""

import pytest


@pytest.mark.asyncio
async def test_set_voice_active_broadcasts_only_on_change():
    from potts.state import AppState

    app_state = AppState()
    events = []

    async def fake_broadcast(event: dict):
        events.append(event)

    app_state.broadcast = fake_broadcast  # type: ignore[method-assign]

    await app_state.set_voice_active(True)
    await app_state.set_voice_active(True)
    await app_state.set_voice_active(False)

    assert events == [
        {"type": "voice_state", "voice_active": True},
        {"type": "voice_state", "voice_active": False},
    ]


@pytest.mark.asyncio
async def test_record_audit_event_tracks_and_broadcasts():
    from potts.state import AppState

    app_state = AppState()
    events = []

    async def fake_broadcast(event: dict):
        events.append(event)

    app_state.broadcast = fake_broadcast  # type: ignore[method-assign]

    await app_state.record_audit_event(
        action="shell",
        target="pwd",
        source="voice_tool",
        status="blocked",
        detail="blocked by policy",
    )

    assert app_state.metrics["blocked_actions"] == 1
    assert app_state.audit_events[0].action == "shell"
    assert events[0]["type"] == "audit_event"
    assert events[0]["event"]["target"] == "pwd"


def test_replace_audit_events_replaces_and_recounts():
    from potts.state import AppState

    app_state = AppState()
    app_state.replace_audit_events([
        {"ts": 1.0, "action": "shell", "target": "pwd", "source": "agent", "status": "blocked", "detail": "blocked"},
        {"ts": 2.0, "action": "write_file", "target": "/tmp/a", "source": "voice_tool", "status": "ok", "detail": ""},
    ])

    assert len(app_state.audit_events) == 2
    assert app_state.metrics["blocked_actions"] == 1
    assert app_state.audit_events[0].target == "pwd"
