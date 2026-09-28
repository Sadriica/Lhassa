# SPDX-License-Identifier: AGPL-3.0-or-later
"""Timed reminders + one-off calendar events; /remind /event.

Moved verbatim out of the former ``lhassa/notify/commands.py``.
"""

from __future__ import annotations

import re

from loguru import logger

from ._registry import register_command
from ._shared import _config, _oops

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


async def _deliver_calendar_event(what: str, when: str, run_at, *, config=None) -> bool:
    """Build + deliver a ``.ics`` for ``what``/``run_at``: best-effort, never
    raises. Returns whether the delivery send succeeded (``False`` on ANY
    trouble; .ics generation, write, or delivery; so a caller can degrade
    silently without breaking its own reply).

    Lazy shell→shell import of :mod:`lhassa.org.deliver` (same pattern as
    ``/cvbase`` reaching into :mod:`lhassa.cv`): the calendar leaf package
    itself never sends anything, this is the one caller that hands the file to
    the sender-of-record seam. ``origin_department=""``: a calendar event is
    Lhassa's own hand, not delegated work, same as ``send_reminder``.
    """
    try:
        from lhassa.calendar import build_event, write_ics

        content = build_event(what, run_at)
        if not content:
            return False
        ics_path = write_ics(content, name=what)

        from lhassa.org.deliver import deliver

        delivered, _artifact_id = await deliver(
            document=ics_path,
            caption=f"📅 {what} · {when}",
            kind="calendar_event",
            metadata={"summary": what, "when": when},
            origin_department="",
            config=config,
        )
        return bool(delivered)
    except Exception as exc:
        logger.warning(f"commands: calendar event delivery failed: {exc}")
        return False


@register_command("remind")
async def _cmd_remind(args: str, *, config=None) -> str:
    """``/remind <what> a las 5pm | at 17:00 | in 30 min [mañana]``: a timed
    reminder through the one clock (a ``send_reminder`` task the heartbeat
    delivers). Deterministic parse, $0; the think never gets involved.

    ADDITIVELY also generates a ``.ics`` for the same event and delivers it
    (a tappable calendar file, on top of the internal reminder); see
    :func:`_deliver_calendar_event`. Best-effort: the internal reminder is
    minted and its reply returned regardless of whether the ``.ics`` step
    succeeds, so a calendar hiccup never breaks ``/remind``.
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

        try:
            await _deliver_calendar_event(what, when, run_at, config=cfg)
        except Exception as exc:  # never let the .ics step break /remind
            logger.warning(f"commands: /remind calendar delivery failed: {exc}")

        local = run_at.astimezone().strftime("%a %H:%M")
        return f"⏰ Noted. I'll remind you {when} ({local}): {what}"
    except Exception as exc:
        logger.warning(f"commands: /remind failed: {exc}")
        return _oops("schedule that reminder")


@register_command("event")
async def _cmd_event(args: str, *, config=None) -> str:
    """``/event <what> a las 5pm | at 17:00 | in 30 min [mañana]``: calendar-only:
    generates and delivers a ``.ics`` for the event, no internal reminder task
    (see ``/remind`` for both). Same deterministic ``parse_remind``, $0."""
    try:
        parsed = parse_remind(args)
        if parsed is None:
            return (
                "Tell me what and when, e.g. /event dentist appointment at 3pm, "
                "/event flight in 2 hours, /event standup mañana 9am."
            )
        run_at, what, when = parsed
        cfg = _config(config)

        ok = await _deliver_calendar_event(what, when, run_at, config=cfg)
        if not ok:
            return _oops("build and send that calendar event")

        local = run_at.astimezone().strftime("%a %H:%M")
        return f"📅 Sent. Calendar event for {when} ({local}): {what}"
    except Exception as exc:
        logger.warning(f"commands: /event failed: {exc}")
        return _oops("build and send that calendar event")
