# SPDX-License-Identifier: AGPL-3.0-or-later
"""The calendar's agenda event source (``lhassa.personal.sources``).

Renders the upcoming ICS events as the personal agenda's display lines. The
calendar name ("· Work") is shown only when the owner has MORE than one
calendar configured; with a single calendar it is redundant noise.
"""

from __future__ import annotations


async def agenda_events(instance_id: str, *, hours: int, config=None) -> list[str]:
    """``"HH:MM Summary[ · Calendar][ · Location]"`` lines; ``[]`` on any trouble."""
    try:
        from lhassa.calendar.feed import _local_hhmm, upcoming

        raw_events = await upcoming(instance_id, hours=hours, config=config)
        calendar_names = {e.get("calendar") for e in raw_events if e.get("calendar")}
        multi = len(calendar_names) > 1
        events: list[str] = []
        for e in raw_events:
            label = f"{_local_hhmm(e['start'])} {e['summary']}"
            if multi and e.get("calendar"):
                label += f" · {e['calendar']}"
            if e.get("location"):
                label += f" · {e['location']}"
            events.append(label)
        return events
    except Exception:
        return []
