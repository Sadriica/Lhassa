"""Tests for lhassa.tools.monitor."""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_monitor_file_change_notifies_on_rewrite(tmp_path, monkeypatch):
    from lhassa.tools import monitor

    target = tmp_path / "events.log"
    target.write_text("hello\n")

    notified = []
    broadcasted = []

    async def fake_notify(message: str, title: str = "Lhassa"):
        notified.append((title, message))

    async def fake_broadcast(event: dict):
        broadcasted.append(event)

    monkeypatch.setattr(monitor, "notify", fake_notify)
    monkeypatch.setattr(monitor.state, "broadcast", fake_broadcast)

    task = asyncio.create_task(monitor._monitor_file_change(target, "events.log"))
    await asyncio.sleep(1.1)
    target.write_text("rewritten\n")
    await asyncio.wait_for(task, timeout=3)

    assert notified == [("Lhassa — File Monitor", "File events.log changed.")]
    assert broadcasted == [{"type": "file_event", "path": str(target), "line": ""}]


@pytest.mark.asyncio
async def test_monitor_file_pattern_handles_rotation(tmp_path, monkeypatch):
    from lhassa.tools import monitor

    target = tmp_path / "app.log"
    target.write_text("before\n")

    notified = []
    broadcasted = []

    async def fake_notify(message: str, title: str = "Lhassa"):
        notified.append((title, message))

    async def fake_broadcast(event: dict):
        broadcasted.append(event)

    monkeypatch.setattr(monitor, "notify", fake_notify)
    monkeypatch.setattr(monitor.state, "broadcast", fake_broadcast)

    task = asyncio.create_task(monitor._monitor_file_pattern(target, "app.log", "ready"))
    await asyncio.sleep(1.1)

    rotated = tmp_path / "app.log.1"
    target.rename(rotated)
    target.write_text("service ready\n")

    await asyncio.wait_for(task, timeout=3)

    assert notified == [("Lhassa — File Monitor", "File app.log: service ready")]
    assert broadcasted == [{"type": "file_event", "path": str(target), "line": "service ready"}]
