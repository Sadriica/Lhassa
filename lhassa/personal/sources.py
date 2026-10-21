# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agenda event sources: where the personal agenda's ``events`` come from.

The agenda read-model (:mod:`lhassa.personal.agenda`) knows nothing about any
particular calendar. A capability pack registers a source here (the calendar
pack registers ``calendar``, the ICS feeds); with none registered the agenda
simply has no events. Leaf: imports only the generic registry.

A source is ``async (instance_id, *, hours, config) -> list[str]``: display
lines (``"HH:MM Summary"`` in local time), already windowed and sorted.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from lhassa.registry import Registry

EventSource = Callable[..., Awaitable[list[str]]]

#: name -> agenda event source. Empty unless a pack registers one.
AGENDA_EVENT_SOURCES: Registry[EventSource] = Registry("agenda event source")


def register_event_source(name: str, fn: EventSource) -> EventSource:
    """Add an agenda event source. Re-registering a name is a no-op."""
    if name not in AGENDA_EVENT_SOURCES:
        AGENDA_EVENT_SOURCES.register(name, fn)
    return fn


async def collect_events(instance_id: str, *, hours: int, config=None) -> list[str]:
    """Every registered source's lines, in registration order. A failing
    source contributes nothing; this never raises."""
    events: list[str] = []
    for _name, fn in AGENDA_EVENT_SOURCES.items().items():
        try:
            lines = await fn(instance_id, hours=hours, config=config)
        except Exception:
            continue
        events.extend(str(line) for line in (lines or []))
    return events


# ── dated events: the same feeds, as structured rows ─────────────────────────
#
# The line sources above are for prose (the briefing flows). Surfaces that lay
# events out by day (the REPL welcome) need the start time itself, so a pack may
# also register a dated source: ``async (instance_id, *, since, until, config)
# -> list[dict]`` with ``start`` (aware datetime), ``end`` (aware or None),
# ``summary``, ``location``, ``calendar`` and ``all_day``.

DatedEventSource = Callable[..., Awaitable[list[dict]]]

#: name -> dated agenda event source. Empty unless a pack registers one.
AGENDA_DATED_SOURCES: Registry[DatedEventSource] = Registry("agenda dated event source")


def register_dated_event_source(name: str, fn: DatedEventSource) -> DatedEventSource:
    """Add a dated agenda event source. Re-registering a name is a no-op."""
    if name not in AGENDA_DATED_SOURCES:
        AGENDA_DATED_SOURCES.register(name, fn)
    return fn


async def collect_dated_events(instance_id: str, *, since, until, config=None) -> list[dict]:
    """Every dated source's events in ``[since, until]``, sorted by start.
    A failing source or a malformed row contributes nothing; never raises."""
    events: list[dict] = []
    for _name, fn in AGENDA_DATED_SOURCES.items().items():
        try:
            rows = await fn(instance_id, since=since, until=until, config=config)
        except Exception:
            continue
        for row in rows or []:
            if isinstance(row, dict) and row.get("start") is not None:
                events.append(row)
    try:
        events.sort(key=lambda e: e["start"])
    except TypeError:
        pass
    return events
