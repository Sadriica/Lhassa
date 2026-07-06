"""Reminder / scheduling tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat.services.llm_service import FunctionCallParams

from datetime import datetime, timedelta, timezone

from loguru import logger

from lhassa.memory.store import add_scheduled_task, load_all_scheduled_tasks
from lhassa.tools.handlers._shared import _bump_tool


async def handle_schedule_reminder(params: FunctionCallParams):
    name = params.arguments.get("name", "Reminder")
    message = params.arguments.get("message", "")
    in_minutes = params.arguments.get("in_minutes")
    at_time = params.arguments.get("at_time")
    _bump_tool()

    now = datetime.now(timezone.utc)

    if in_minutes is not None:
        run_at = now + timedelta(minutes=int(in_minutes))
        when_str = f"in {in_minutes} minute{'s' if in_minutes != 1 else ''}"
    elif at_time:
        try:
            if len(at_time) <= 5 and ":" in at_time:
                # HH:MM interpreted as local time; convert to UTC-aware datetime
                h, m = at_time.split(":")
                local_naive = datetime.now().replace(
                    hour=int(h), minute=int(m), second=0, microsecond=0
                )
                run_at = local_naive.astimezone(timezone.utc)
                if run_at <= now:
                    run_at += timedelta(days=1)
                when_str = f"at {at_time}"
            else:
                run_at = datetime.fromisoformat(at_time)
                if run_at.tzinfo is None:
                    run_at = run_at.astimezone(timezone.utc)
                when_str = f"at {at_time}"
        except Exception:
            await params.result_callback(
                f"Could not parse time: {at_time!r}. Use HH:MM or ISO 8601."
            )
            return
    else:
        await params.result_callback(
            "Please specify either in_minutes or at_time for the reminder."
        )
        return

    task_id = await add_scheduled_task(name, run_at.isoformat(), message)
    logger.info(f"[scheduler] Task #{task_id} '{name}' scheduled for {run_at.isoformat()}")
    await params.result_callback(f"Reminder '{name}' scheduled {when_str}.")


async def handle_list_reminders(params: FunctionCallParams):
    _bump_tool()
    tasks = await load_all_scheduled_tasks()
    pending = [t for t in tasks if t["status"] == "pending"]

    if not pending:
        await params.result_callback("No pending reminders.")
        return

    lines = [
        f"#{t['id']} '{t['name']}' — {t['run_at'][:16].replace('T', ' ')}: {t['message']}"
        for t in pending
    ]
    await params.result_callback("\n".join(lines))
