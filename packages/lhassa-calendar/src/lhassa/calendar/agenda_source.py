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


async def dated_events(instance_id: str, *, since, until, config=None) -> list[dict]:
    """Structured events in ``[since, until]`` for day-by-day layouts; ``[]`` on any trouble."""
    try:
        from datetime import datetime, timezone

        from lhassa.calendar.feed import upcoming

        now = datetime.now(timezone.utc)
        hours = max(1, int((until - now).total_seconds() // 3600) + 1)
        rows = await upcoming(instance_id, hours=hours, since=since, config=config)
        return [
            {
                "start": e["start"],
                "end": e.get("end"),
                "summary": str(e.get("summary") or ""),
                "location": str(e.get("location") or ""),
                "calendar": str(e.get("calendar") or ""),
                "all_day": bool(e.get("all_day")),
            }
            for e in rows
            if e.get("start") is not None and since <= e["start"] <= until
        ]
    except Exception:
        return []
