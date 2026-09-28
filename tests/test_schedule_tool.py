"""Tests for lhassa/tools/handlers/schedule.py.

``schedule_reminder`` / ``list_reminders`` now mint/read ``send_reminder``
tasks in the ``tasks`` table (``core/db.py``) instead of writing to the legacy
``memory/store.py`` ``scheduled_tasks`` table. Fully offline: DB paths are
redirected to tmp files by the autouse ``_isolate_brain_db`` fixture
(``tests/conftest.py``) plus a local ``tmp_db`` fixture for the legacy store.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock


def _run(coro):
    return asyncio.run(coro)


class _FakeParams:
    """A minimal stand-in for pipecat's FunctionCallParams."""

    def __init__(self, **arguments):
        self.arguments = arguments
        self.result_callback = AsyncMock()


# ---------------------------------------------------------------------------
# schedule_reminder; mints a send_reminder task
# ---------------------------------------------------------------------------


def test_schedule_reminder_mints_send_reminder_task_in_minutes(monkeypatch):
    from lhassa.tools.handlers import schedule as sched_mod

    captured = {}

    async def fake_mint_task(instance_id, accion, objetivo="", params=None, *, origin, due_at=None, recurrence=None, judgment=False):
        captured.update(
            instance_id=instance_id,
            accion=accion,
            objetivo=objetivo,
            params=params,
            due_at=due_at,
            recurrence=recurrence,
        )
        return ("t_test123", "pendiente")

    monkeypatch.setattr("lhassa.core.dispatch.mint_task", fake_mint_task)

    params = _FakeParams(name="call John", message="Time to call John", in_minutes=20)
    before = datetime.now(timezone.utc)
    _run(sched_mod.handle_schedule_reminder(params))
    after = datetime.now(timezone.utc)

    assert captured["accion"] == "send_reminder"
    assert captured["objetivo"] == "call John"
    assert captured["params"] == {"text": "Time to call John"}
    assert captured["recurrence"] is None

    due_at = datetime.fromisoformat(captured["due_at"])
    assert before + timedelta(minutes=19, seconds=55) <= due_at <= after + timedelta(minutes=20, seconds=5)

    params.result_callback.assert_awaited_once()
    (msg,), _ = params.result_callback.call_args
    assert "call John" in msg
    assert "20 minutes" in msg


def test_schedule_reminder_mints_at_absolute_time(monkeypatch):
    from lhassa.tools.handlers import schedule as sched_mod

    captured = {}

    async def fake_mint_task(instance_id, accion, objetivo="", params=None, *, origin, due_at=None, recurrence=None, judgment=False):
        captured.update(due_at=due_at, accion=accion)
        return ("t_test456", "pendiente")

    monkeypatch.setattr("lhassa.core.dispatch.mint_task", fake_mint_task)

    at_time = "2026-04-01T14:00:00+02:00"
    params = _FakeParams(name="standup", message="join the standup", at_time=at_time)
    _run(sched_mod.handle_schedule_reminder(params))

    assert captured["accion"] == "send_reminder"
    assert captured["due_at"] == at_time
    params.result_callback.assert_awaited_once()


def test_schedule_reminder_reports_mint_failure(monkeypatch):
    from lhassa.tools.handlers import schedule as sched_mod

    async def fake_mint_task(*a, **k):
        return ("", "refused: unknown accion 'send_reminder'")

    monkeypatch.setattr("lhassa.core.dispatch.mint_task", fake_mint_task)

    params = _FakeParams(name="x", message="y", in_minutes=5)
    _run(sched_mod.handle_schedule_reminder(params))

    params.result_callback.assert_awaited_once()
    (msg,), _ = params.result_callback.call_args
    assert "Could not schedule" in msg


def test_schedule_reminder_requires_time():
    from lhassa.tools.handlers import schedule as sched_mod

    params = _FakeParams(name="x", message="y")
    _run(sched_mod.handle_schedule_reminder(params))

    params.result_callback.assert_awaited_once()
    (msg,), _ = params.result_callback.call_args
    assert "in_minutes or at_time" in msg


# ---------------------------------------------------------------------------
# list_reminders; reads pending send_reminder tasks
# ---------------------------------------------------------------------------


def test_list_reminders_shows_minted_task(monkeypatch):
    from lhassa.tools.handlers import schedule as sched_mod
    import lhassa.core.db as db

    _run(db.init_db())
    inst = "inst_personal"
    monkeypatch.setattr(sched_mod, "_resolve_instance_id", lambda: inst)

    _run(
        db.add_task(
            inst,
            "send_reminder",
            objetivo="call John",
            params={"text": "Time to call John"},
            due_at="2026-04-01T14:20:00+00:00",
        )
    )

    params = _FakeParams()
    _run(sched_mod.handle_list_reminders(params))

    params.result_callback.assert_awaited_once()
    (msg,), _ = params.result_callback.call_args
    assert "call John" in msg
    assert "Time to call John" in msg


def test_list_reminders_ignores_other_actions_and_done_tasks(monkeypatch):
    from lhassa.tools.handlers import schedule as sched_mod
    import lhassa.core.db as db

    _run(db.init_db())
    inst = "inst_personal"
    monkeypatch.setattr(sched_mod, "_resolve_instance_id", lambda: inst)

    _run(db.add_task(inst, "notify", objetivo="not a reminder", params={"text": "x"}))
    done_id = _run(
        db.add_task(inst, "send_reminder", objetivo="already fired", params={"text": "x"})
    )
    _run(db.set_task_state(inst, done_id, "hecha"))

    params = _FakeParams()
    _run(sched_mod.handle_list_reminders(params))

    (msg,), _ = params.result_callback.call_args
    assert msg == "No pending reminders."


def test_list_reminders_empty():
    from lhassa.tools.handlers import schedule as sched_mod

    params = _FakeParams()
    _run(sched_mod.handle_list_reminders(params))

    params.result_callback.assert_awaited_once_with("No pending reminders.")
