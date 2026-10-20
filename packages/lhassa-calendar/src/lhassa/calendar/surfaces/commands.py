# SPDX-License-Identifier: AGPL-3.0-or-later
"""The calendar pack's chat commands: /calendar /calendars /event.

Named calendar feeds, the master-calendar reconcile, and the calendar-only
``/event`` door. Also owns the ``.ics`` extra that core's ``/remind`` runs
through its reminder-extras seam (:func:`deliver_calendar_event`), so a
reminder still works when this pack is not installed; it just carries no file.

An owner can subscribe several calendars (institutional, personal, a project
one), each with a name, so /today shows which calendar an event is from (see
lhassa.calendar.store). These commands never touch the network; they only
edit the JSON store; the next /today simply picks up the new list.
"""

from __future__ import annotations

import re

from loguru import logger

from lhassa.notify.commands._registry import register_command
from lhassa.notify.commands._remind import parse_remind
from lhassa.notify.commands._shared import _config, _oops


def _mask_url(url: str) -> str:
    """A short, non-secret preview of a feed url; never the full thing.

    Calendar URLs carry read access to the owner's calendar, so /calendars must
    never echo one back in full. Shows scheme+host plus a short prefix of the
    path, elided.
    """
    url = (url or "").strip()
    if len(url) <= 40:
        return url
    return url[:40] + "…"


@register_command("calendars")
async def _cmd_calendars(args: str, *, config=None) -> str:
    """``/calendars``: list configured named calendars (name + a masked url)."""
    try:
        cfg = _config(config)
        from lhassa.calendar import store as _cal_store

        items = _cal_store.all_feeds(cfg)
        if not items:
            return (
                "No calendars configured yet. Use /calendar add <name> <url> to "
                "subscribe one."
            )
        primary = _cal_store.get_primary(cfg)
        lines = [f"Calendars ({len(items)}):"]
        for it in items:
            name = it.get("name")
            marker = " (primary)" if primary and name == primary else ""
            lines.append(f"- {name}{marker}: {_mask_url(it.get('url', ''))}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /calendars failed: {exc}")
        return _oops("list your calendars")


@register_command("calendar")
async def _cmd_calendar(args: str, *, config=None) -> str:
    """``/calendar add <name> <url> | remove <name> | primary <name> | sync``;
    manage named calendar feeds and the master-calendar reconcile.

    ``add``/``remove`` never touch the network; they only edit the JSON store
    (:mod:`lhassa.calendar.store`); the feed is fetched lazily, the next time
    /today (or /calendar sync) runs. ``primary`` designates the MASTER calendar
    (see :mod:`lhassa.calendar.reconcile`); ``sync`` runs the $0 diff now and
    reports what it found/delivered.
    """
    text = (args or "").strip()
    if not text:
        return (
            "Usage: /calendar add <name> <url>; subscribe a named calendar.\n"
            "       /calendar remove <name>; drop one.\n"
            "       /calendar primary <name>; set your master calendar.\n"
            "       /calendar sync; find events missing from the master.\n"
            "       /calendar view <when>; e.g. 'friday', 'esta semana', 'hoy'.\n"
            "See /calendars to list what's configured."
        )
    parts = text.split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    cfg = _config(config)
    try:
        from lhassa.calendar import store as _cal_store

        if sub == "add":
            add_parts = rest.split(maxsplit=1)
            if len(add_parts) < 2:
                return "Usage: /calendar add <name> <url>"
            name, url = add_parts[0], add_parts[1].strip()
            ok = _cal_store.add_calendar(name, url, config=cfg)
            if not ok:
                return (
                    "Couldn't add that; check the name isn't empty and the url "
                    "starts with http:// or https://."
                )
            return f"Calendar added: {name}. See /calendars, /today."

        if sub == "remove":
            name = rest.strip()
            if not name:
                return "Usage: /calendar remove <name>"
            ok = _cal_store.remove_calendar(name, config=cfg)
            if not ok:
                return f"No calendar named {name!r}. See /calendars."
            return f"Calendar removed: {name}."

        if sub == "primary":
            name = rest.strip()
            if not name:
                return "Usage: /calendar primary <name>"
            ok = _cal_store.set_primary(name, config=cfg)
            if not ok:
                return f"No calendar named {name!r}. See /calendars."
            return f"Master calendar set: {name}. /calendar sync will diff every other calendar against it."

        if sub == "sync":
            from lhassa.calendar.surfaces.actions import calendar_sync

            instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
            summary = await calendar_sync({"instance_id": instance_id, "config": cfg})
            return _calendar_sync_reply(summary)

        if sub in ("view", "on", "when"):
            when = rest.strip()
            if not when:
                return "Usage: /calendar view <when>; e.g. 'friday', 'esta semana', 'hoy'."
            from lhassa.calendar import query as _cal_query

            instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
            # Force a calendar answer even for a bare time phrase with no
            # calendar keyword ("/calendar view friday"); the command itself
            # already establishes intent, so route straight past
            # is_calendar_query's stricter chat-message heuristic.
            reply = await _cal_query.answer(instance_id, f"calendar {when}", config=cfg)
            if reply is None:
                return (
                    f"Couldn't work out a time reference in {when!r}. Try "
                    "'friday', 'tomorrow', 'esta semana', 'hoy'…"
                )
            return reply

        return (
            f"Unknown /calendar sub-command {sub!r}. Usage: /calendar add <name> "
            "<url> | remove <name> | primary <name> | sync | view <when>."
        )
    except Exception as exc:
        logger.warning(f"commands: /calendar {sub} failed: {exc}")
        return _oops("manage your calendars")


_CALENDAR_SYNC_RE = re.compile(
    r"^calendar_sync: (\d+) unsynced event\(s\), (\d+) delivered$"
)


def _calendar_sync_reply(summary: str) -> str:
    """Render :func:`lhassa.calendar.surfaces.actions.calendar_sync`'s plain
    outcome string in the Telegram voice. Falls back to the raw string for the
    "no primary" / "nothing to sync" / error outcomes; those already read
    fine verbatim."""
    m = _CALENDAR_SYNC_RE.match(summary or "")
    if not m:
        return summary
    n, delivered = int(m.group(1)), int(m.group(2))
    if n == 0:
        return "Your other calendars are already in sync with your master calendar."
    return (
        f"I found {n} event(s) on your other calendars not in your master "
        f"calendar; sent you {delivered} to add."
    )


async def deliver_calendar_event(what: str, when: str, run_at, *, config=None) -> bool:
    """Build + deliver a ``.ics`` for ``what``/``run_at``: best-effort, never
    raises. Returns whether the delivery send succeeded (``False`` on ANY
    trouble: .ics generation, write, or delivery), so a caller can degrade
    silently without breaking its own reply.

    The calendar leaf package itself never sends anything; this surface hands
    the file to the sender-of-record seam (:func:`lhassa.org.deliver.deliver`).
    ``origin_department=""``: a calendar event is Lhassa's own hand, not
    delegated work, same as ``send_reminder``. Registered as core's
    ``calendar_event`` reminder extra, so ``/remind`` gets a file too.
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

        ok = await deliver_calendar_event(what, when, run_at, config=cfg)
        if not ok:
            return _oops("build and send that calendar event")

        local = run_at.astimezone().strftime("%a %H:%M")
        return f"📅 Sent. Calendar event for {when} ({local}): {what}"
    except Exception as exc:
        logger.warning(f"commands: /event failed: {exc}")
        return _oops("build and send that calendar event")
