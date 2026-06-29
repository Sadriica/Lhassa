"""Tests for lhassa.scheduler."""

import pytest


@pytest.mark.asyncio
async def test_scheduler_does_not_mark_done_when_notify_fails(monkeypatch):
    import lhassa.scheduler as scheduler

    calls: list[tuple[str, object]] = []

    async def fake_load_pending_tasks():
        return [{"id": 1, "name": "reminder", "message": "ping"}]

    async def fake_notify(message: str, title: str = "Lhassa"):
        raise RuntimeError("speaker offline")

    async def fake_mark_task_done(task_id: int):
        calls.append(("mark_done", task_id))

    async def fake_broadcast(event: dict):
        calls.append(("broadcast", event))

    async def fake_sleep(_seconds: float):
        raise asyncio.CancelledError()

    import asyncio

    monkeypatch.setattr(scheduler, "load_pending_tasks", fake_load_pending_tasks)
    monkeypatch.setattr(scheduler, "notify", fake_notify)
    monkeypatch.setattr(scheduler, "mark_task_done", fake_mark_task_done)
    monkeypatch.setattr(scheduler.state, "broadcast", fake_broadcast)
    monkeypatch.setattr(scheduler.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_scheduler()

    assert calls == []


@pytest.mark.asyncio
async def test_scheduler_marks_done_after_successful_notify(monkeypatch):
    import lhassa.scheduler as scheduler

    calls: list[tuple[str, object]] = []

    async def fake_load_pending_tasks():
        return [{"id": 7, "name": "reminder", "message": "ping", "status": "pending"}]

    async def fake_notify(message: str, title: str = "Lhassa"):
        calls.append(("notify", message))

    async def fake_mark_task_done(task_id: int):
        calls.append(("mark_done", task_id))

    async def fake_broadcast(event: dict):
        calls.append(("broadcast", event))

    async def fake_sleep(_seconds: float):
        raise asyncio.CancelledError()

    import asyncio

    monkeypatch.setattr(scheduler, "load_pending_tasks", fake_load_pending_tasks)
    monkeypatch.setattr(scheduler, "notify", fake_notify)
    monkeypatch.setattr(scheduler, "mark_task_done", fake_mark_task_done)
    monkeypatch.setattr(scheduler.state, "broadcast", fake_broadcast)
    monkeypatch.setattr(scheduler.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_scheduler()

    assert calls[0] == ("notify", "ping")
    assert calls[1] == ("mark_done", 7)
    assert calls[2][0] == "broadcast"
    assert calls[2][1]["task"]["status"] == "done"
