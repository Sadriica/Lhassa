# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reminder / scheduling tool handlers.

``schedule_reminder`` / ``list_reminders`` mint and read ``send_reminder`` tasks
in the ``tasks`` table (``core/db.py``) via ``core.dispatch.mint_task``: the
same do-and-sleep backbone the heartbeat sweeps every tick
(``core/watchlist.py:_from_pending_tasks``). This tool no longer writes to the
legacy ``memory/store.py`` ``scheduled_tasks`` table; see ``doc/scheduler.md``
and ``migrate_legacy_reminders`` below for the one-shot migration off it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat.services.llm_service import FunctionCallParams

from datetime import datetime, timedelta, timezone

from loguru import logger

from lhassa.tools.handlers._shared import _bump_tool

_SEND_REMINDER_ACCION = "send_reminder"
_LEGACY_MIGRATION_WATCH_KEY = "legacy_reminder_migration_v1"


def _resolve_instance_id() -> str:
    from lhassa.config import Config

    return getattr(Config, "instance_id", None) or "inst_personal"


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

    from lhassa.core import db as _db
    from lhassa.core.dispatch import mint_task
    from lhassa.security.trust import Origin, TrustLevel

    instance_id = _resolve_instance_id()
    await _db.init_db()

    origin = Origin(trust=TrustLevel.HUMANO, actor_id="voice")
    task_id, estado = await mint_task(
        instance_id,
        _SEND_REMINDER_ACCION,
        objetivo=name,
        params={"text": message},
        origin=origin,
        due_at=run_at.isoformat(),
    )

    if not task_id:
        logger.error(f"[reminder] mint_task failed for '{name}': {estado}")
        await params.result_callback(f"Could not schedule reminder '{name}': {estado}")
        return

    logger.info(
        f"[reminder] Task {task_id} '{name}' scheduled for {run_at.isoformat()} (estado={estado})"
    )
    await params.result_callback(f"Reminder '{name}' scheduled {when_str}.")


async def handle_list_reminders(params: FunctionCallParams):
    _bump_tool()

    from lhassa.core import db as _db

    instance_id = _resolve_instance_id()
    await _db.init_db()

    tasks = await _db.list_tasks(instance_id, estado="pendiente")
    pending = [t for t in tasks if t.get("accion") == _SEND_REMINDER_ACCION]
    pending.sort(key=lambda t: t.get("due_at") or "")

    if not pending:
        await params.result_callback("No pending reminders.")
        return

    lines = [
        "#{id} '{name}': {when}: {message}".format(
            id=t.get("id"),
            name=t.get("objetivo") or "Reminder",
            when=(t.get("due_at") or "")[:16].replace("T", " "),
            message=(t.get("params") or {}).get("text", ""),
        )
        for t in pending
    ]
    await params.result_callback("\n".join(lines))


# ── One-shot legacy migration ────────────────────────────────────────────────
#
# The reminder tool used to write directly into memory/store.py's
# ``scheduled_tasks`` table, fired by the now-retired lhassa/scheduler.py 30s
# poller. This copies any still-pending legacy rows into the real tasks table
# as ``send_reminder`` tasks (so the heartbeat picks them up) and marks the
# legacy rows ``migrated`` (never deleted; they remain visible for audit /
# the memory export-import snapshot). Idempotent: guarded by a watch_state
# marker, so running it twice (or once per process restart) is a no-op the
# second time.


async def migrate_legacy_reminders(instance_id: str | None = None) -> str:
    """Copy pending legacy reminders into the tasks table exactly once.

    Never raises; any failure is logged and reported in the returned string,
    startup must not be blocked by this. Safe to call from any long-lived
    process entrypoint (main.py, docker/run_api.py); a second call after the
    marker is set is a cheap no-op (one watch_state read, no writes).
    """
    instance_id = instance_id or _resolve_instance_id()
    try:
        from lhassa.core import db as _db

        await _db.init_db()

        already = await _db.get_watch_state(instance_id, _LEGACY_MIGRATION_WATCH_KEY)
        if already:
            return "reminder migration: already applied, skipping"

        from lhassa._vendor import aiosqlite
        from lhassa.core.dispatch import mint_task
        from lhassa.memory.store import DB_PATH as _LEGACY_DB_PATH
        from lhassa.memory.store import init_db as _init_legacy_db
        from lhassa.memory.store import load_all_scheduled_tasks
        from lhassa.security.trust import Origin, TrustLevel

        await _init_legacy_db()
        legacy_tasks = await load_all_scheduled_tasks()
        legacy_pending = [t for t in legacy_tasks if t.get("status") == "pending"]

        migrated = 0
        origin = Origin(trust=TrustLevel.HUMANO, actor_id="legacy-migration")
        for legacy in legacy_pending:
            text = str(legacy.get("message") or "").strip()
            name = str(legacy.get("name") or "Reminder").strip() or "Reminder"
            run_at = legacy.get("run_at")
            if not text or not run_at:
                continue

            task_id, _estado = await mint_task(
                instance_id,
                _SEND_REMINDER_ACCION,
                objetivo=name,
                params={"text": text},
                origin=origin,
                due_at=run_at,
            )
            if not task_id:
                continue

            # Mark the legacy row migrated (status update only; never deleted).
            # Raw SQL against the legacy table: memory/store.py is not touched,
            # this reuses its DB_PATH constant only.
            async with aiosqlite.connect(_LEGACY_DB_PATH) as legacy_db:
                await legacy_db.execute(
                    "UPDATE scheduled_tasks SET status='migrated' WHERE id=?",
                    (legacy["id"],),
                )
                await legacy_db.commit()
            migrated += 1

        await _db.set_watch_state(instance_id, _LEGACY_MIGRATION_WATCH_KEY, "done")
        summary = f"reminder migration: migrated {migrated} legacy reminder(s)"
        logger.info(f"[reminder] {summary}")
        return summary

    except Exception as exc:
        logger.warning(f"[reminder] legacy migration failed: {exc}")
        return f"reminder migration: error: {exc}"
