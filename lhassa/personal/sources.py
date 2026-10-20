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
