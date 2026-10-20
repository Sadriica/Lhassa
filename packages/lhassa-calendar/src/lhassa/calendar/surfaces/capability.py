# SPDX-License-Identifier: AGPL-3.0-or-later
"""``calendar_view``: the calendar pack's entry in the capability catalog.

Registered into :mod:`lhassa.notify.capabilities` by the pack. It carries:

  * a recognizer + handler (:func:`lhassa.calendar.query.is_calendar_query` /
    :func:`lhassa.calendar.query.answer`) for the route table;
  * an always-on ``shortcut``: a recognised calendar question with a parseable
    time reference is answered before any think, $0, logged as
    ``calendar-query: <window>``;
  * a ``continue_followup`` for elliptical follow-ups ("Y el sábado?",
    "¿y a las 6am?") once the sticky topic is the calendar;
  * a ``topic_context``: the day the message resolved to, so a bare-time
    follow-up targets the right day.
"""

from __future__ import annotations

from typing import Optional

from lhassa.notify.capabilities import Capability, register_capability


def _instance(config) -> str:
    return getattr(config, "instance_id", None) or "inst_personal"


async def _calendar_handler(text: str, *, config=None) -> Optional[str]:
    """The ``calendar_view`` deterministic handler; $0, no model. ``None`` when
    the calendar seam can't resolve an answer (no parseable window, etc.)."""
    from lhassa.calendar import query as _cal_query

    return await _cal_query.answer(_instance(config), text, config=config)


def _calendar_recognizer(text: str) -> bool:
    from lhassa.calendar.query import is_calendar_query

    return is_calendar_query(text)


async def _calendar_shortcut(text: str, *, config=None) -> Optional[tuple[str, str]]:
    """The always-on calendar answer plus its episode decision; ``None`` when
    :mod:`lhassa.calendar.query` doesn't recognise ``text``. Never raises."""
    from lhassa.calendar import query as _cal_query

    try:
        reply = await _cal_query.answer(_instance(config), text, config=config)
    except Exception:
        return None
    if reply is None:
        return None
    try:
        window = _cal_query.parse_window(text)
        label = window[2] if window else "?"
    except Exception:
        label = "?"
    return reply, f"calendar-query: {label}"


def _calendar_topic_context(text: str) -> dict:
    """The day ``text`` resolved to (``{"date": iso}``); falsy when none."""
    from lhassa.calendar import query as _cal_query

    return {"date": _cal_query.resolved_date(text)}


async def _calendar_continue_followup(
    followup_text: str, context: dict, *, config=None
) -> Optional[str]:
    """The ``calendar_view`` follow-up continuation; $0, no model.

    "Y el sábado?" carries no calendar keyword, so the plain handler would
    refuse it. Once the STICKY topic says the thread is already about the
    calendar, the follow-up's OWN reference is enough. Two shapes, in order:

      1. A DAY follow-up ("Y el sábado?"): :func:`lhassa.calendar.query.
         answer_window`, which parses/answers WITHOUT the keyword gate.
      2. A bare TIME follow-up ("¿y a las 6am?"): :func:`lhassa.calendar.
         query.answer_time_of_day`, resolved against the sticky day carried in
         ``context["date"]`` (the last calendar answer's day; today by
         default).

    ``None`` only when the follow-up carries NEITHER a parseable day nor a
    time; the caller then falls through to the normal chat path. Never raises.
    """
    from lhassa.calendar import query as _cal_query

    instance_id = _instance(config)
    reply = await _cal_query.answer_window(instance_id, followup_text, config=config)
    if reply is not None:
        return reply
    day_iso = (context or {}).get("date")
    return await _cal_query.answer_time_of_day(
        instance_id, followup_text, day_iso=day_iso, config=config
    )


CALENDAR_VIEW = Capability(
    "calendar_view",
    _calendar_recognizer,
    "Answer 'what's on my calendar' style questions straight from the "
    "calendar feed; $0, no model, no web search.",
    _calendar_handler,
    continue_followup=_calendar_continue_followup,
    shortcut=_calendar_shortcut,
    topic_context=_calendar_topic_context,
)


def register() -> None:
    """Add ``calendar_view`` to the catalog. Idempotent."""
    register_capability(CALENDAR_VIEW)
