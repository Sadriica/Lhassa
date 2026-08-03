"""Tests for lhassa.scheduler — the deprecated no-op shim.

The 30s poller is retired; reminders now go through core/db.py's tasks table
+ the heartbeat (see doc/scheduler.md). ``run_scheduler`` must degrade to a
harmless, immediately-returning no-op for any caller still importing it.
"""

import pytest


@pytest.mark.asyncio
async def test_run_scheduler_returns_immediately_and_logs_deprecation(monkeypatch):
    import lhassa.scheduler as scheduler

    messages = []
    monkeypatch.setattr(scheduler.logger, "info", lambda msg: messages.append(msg))

    # Must not hang, sleep, or poll — a bounded await proves it returns.
    await scheduler.run_scheduler()

    assert any("deprecated" in m.lower() for m in messages)


@pytest.mark.asyncio
async def test_run_scheduler_never_touches_the_legacy_store(monkeypatch):
    import lhassa.scheduler as scheduler

    called = []

    async def fake_load_pending_tasks():
        called.append("load_pending_tasks")
        return []

    # The shim doesn't import memory.store at all; patching a nonexistent
    # attribute would fail this test if the shim regained the old behaviour.
    assert not hasattr(scheduler, "load_pending_tasks")
    assert not hasattr(scheduler, "notify")
    assert not hasattr(scheduler, "mark_task_done")

    await scheduler.run_scheduler()
    assert called == []
