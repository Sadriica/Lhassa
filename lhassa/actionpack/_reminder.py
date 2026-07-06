"""The ``send_reminder`` action — delivers a reminder minted by ``schedule_reminder``.

A thin ``(params) -> str`` wrapper over :func:`lhassa.notify.dispatch.notify`, in
the same shape as ``actionpack/_digest.py``/``actionpack/_brain.py:notify`` — every
action is a plain callable, never raises, returns a short outcome string.

This is the fixed action side of the reminder path: the voice/CLI/Telegram
``schedule_reminder`` tool (``tools/handlers/schedule.py``) mints a
``send_reminder`` task (via ``core.dispatch.mint_task``, with ``due_at``) instead
of writing to the legacy ``memory/store.py`` ``scheduled_tasks`` table. The
heartbeat's watchlist (``core/watchlist.py:_from_pending_tasks``) picks the task
up once its ``due_at`` has passed and runs this action — zero tokens, deterministic,
observable as a normal Episode. See ``doc/scheduler.md``.
"""

from __future__ import annotations

from lhassa.notify.dispatch import notify as _notify


async def send_reminder(params: dict) -> str:
    """Deliver a reminder's text over the configured notify channels.

    params:
        text  (required) — the reminder message to deliver.

    Returns "send_reminder: delivered", "send_reminder: no channel delivered",
    "send_reminder: no text" (empty/missing text), or an error string. Never
    raises.
    """
    try:
        text = str((params or {}).get("text") or "").strip()
        if not text:
            return "send_reminder: no text"

        delivered = await _notify(text, level="info")
        return "send_reminder: delivered" if delivered else "send_reminder: no channel delivered"

    except Exception as exc:
        return f"send_reminder: error: {exc}"
