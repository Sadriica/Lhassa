# SPDX-License-Identifier: AGPL-3.0-or-later
"""Timed reminders; /remind, plus the reminder-extras seam.

A reminder is core: a ``send_reminder`` task on the one clock (tasks +
heartbeat). Anything a pack wants to add on top of a minted reminder (the
calendar pack sends a tappable ``.ics``) plugs in through
:func:`register_reminder_extra`; with no extra registered, ``/remind`` simply
mints the reminder and replies.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from loguru import logger

from lhassa.registry import Registry

from ._registry import register_command
from ._shared import _config, _oops

#: ``(what, when, run_at, *, config) -> bool`` coroutine run after a reminder is
#: minted. Best-effort: a failing or raising extra never breaks ``/remind``.
ReminderExtra = Callable[..., Awaitable[Any]]

#: name -> reminder extra. Empty unless a pack registers one.
REMINDER_EXTRAS: Registry[ReminderExtra] = Registry("reminder extra")


def register_reminder_extra(name: str, fn: ReminderExtra) -> ReminderExtra:
    """Run ``fn`` after every ``/remind`` mint. Re-registering a name is a no-op."""
    if name not in REMINDER_EXTRAS:
        REMINDER_EXTRAS.register(name, fn)
    return fn


async def _run_reminder_extras(what: str, when: str, run_at, *, config=None) -> None:
    """Run every registered extra; never raises."""
    for name, fn in REMINDER_EXTRAS.items().items():
        try:
            await fn(what, when, run_at, config=config)
        except Exception as exc:  # never let an extra break /remind
            logger.warning(f"commands: /remind extra {name!r} failed: {exc}")

# Time phrases understood, in order of specificity. All local-time; conversion to
# UTC mirrors the schedule tool (a past time today rolls to tomorrow).
_REMIND_AT_RE = re.compile(
    r"(?:\b(?:a\s+las?|at)\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b"
    r"|(?:\b(?:a\s+las?|at)\s+)(\d{1,2})(?::(\d{2}))?\b",
    re.IGNORECASE,
)
_REMIND_IN_RE = re.compile(
    r"\b(?:en|in)\s+(\d+)\s*(min(?:uto)?s?|m|h|horas?|hours?)\b", re.IGNORECASE
)
_REMIND_TOMORROW_RE = re.compile(r"\b(mañana|tomorrow)\b", re.IGNORECASE)
# Leading filler after the trigger word: "de", "que", "de que", "to".
_REMIND_FILLER_RE = re.compile(r"^(?:de\s+que\s+|de\s+|que\s+|to\s+)", re.IGNORECASE)


def parse_remind(text: str, *, now=None):
    """Parse '<what> a las 5pm' / 'in 30 min <what>' → ``(run_at_utc, what, when)``.

    Deterministic, $0, local-time aware: bare hours honor am/pm, a past time
    today rolls to tomorrow, 'mañana/tomorrow' adds a day. Returns ``None`` when
    no time phrase is found (the caller decides what to say). The matched time
    words are stripped from the reminder text.
    """
    from datetime import datetime, timedelta, timezone

    raw = " ".join((text or "").split())
    if not raw:
        return None
    now_local = now or datetime.now().astimezone()

    m = _REMIND_IN_RE.search(raw)
    if m:
        qty = int(m.group(1))
        unit = m.group(2).lower()
        delta = timedelta(hours=qty) if unit.startswith(("h",)) else timedelta(minutes=qty)
        run_at = now_local + delta
        what = (raw[: m.start()] + raw[m.end():]).strip()
        when = f"in {qty} {'h' if unit.startswith('h') else 'min'}"
    else:
        m = _REMIND_AT_RE.search(raw)
        if not m:
            return None
        hour = int(m.group(1) or m.group(4))
        minute = int(m.group(2) or m.group(5) or 0)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        run_at = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        what = (raw[: m.start()] + raw[m.end():]).strip()
        tomorrow = _REMIND_TOMORROW_RE.search(what)
        if tomorrow:
            what = (what[: tomorrow.start()] + what[tomorrow.end():]).strip()
            run_at += timedelta(days=1)
        elif run_at <= now_local:
            run_at += timedelta(days=1)
        when = f"at {hour:02d}:{minute:02d}"

    what = _REMIND_FILLER_RE.sub("", what).strip(" ,.-") or "Reminder"
    return (run_at.astimezone(timezone.utc), what[:300], when)


@register_command("remind")
async def _cmd_remind(args: str, *, config=None) -> str:
    """``/remind <what> a las 5pm | at 17:00 | in 30 min [mañana]``: a timed
    reminder through the one clock (a ``send_reminder`` task the heartbeat
    delivers). Deterministic parse, $0; the think never gets involved.

    Then runs every registered reminder extra (the calendar pack delivers a
    ``.ics`` for the same event). Best-effort: the internal reminder is minted
    and its reply returned regardless of whether an extra succeeds, so a
    calendar hiccup (or no calendar pack at all) never breaks ``/remind``.
    """
    try:
        parsed = parse_remind(args)
        if parsed is None:
            return (
                "Tell me what and when, e.g. /remind llamar al banco a las 5pm, "
                "/remind stretch in 45 min, /remind pagar arriendo mañana 9am."
            )
        run_at, what, when = parsed

        from lhassa.core import db as _db
        from lhassa.core.dispatch import mint_task
        from lhassa.security.trust import Origin, TrustLevel

        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        await _db.init_db()
        task_id, estado = await mint_task(
            instance_id,
            "send_reminder",
            objetivo=what[:80],
            params={"text": f"⏰ {what}"},
            origin=Origin(trust=TrustLevel.HUMANO, actor_id="telegram"),
            due_at=run_at.isoformat(),
        )
        if not task_id:
            return _oops(f"schedule that reminder ({estado})")

        await _run_reminder_extras(what, when, run_at, config=cfg)

        local = run_at.astimezone().strftime("%a %H:%M")
        return f"⏰ Noted. I'll remind you {when} ({local}): {what}"
    except Exception as exc:
        logger.warning(f"commands: /remind failed: {exc}")
        return _oops("schedule that reminder")
