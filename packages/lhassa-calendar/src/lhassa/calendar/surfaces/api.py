# SPDX-License-Identifier: AGPL-3.0-or-later
"""FastAPI routes for the calendar seam; upcoming events + configured feeds.

A thin shell over :mod:`lhassa.calendar` (leaf infra) and
:mod:`lhassa.calendar.surfaces.actions` (the master-calendar reconcile action):
this module only shapes requests/responses and NEVER lets a store/feed hiccup
turn into a 500; every route degrades to an empty/typed default, matching the
house style of the other ``*_routes`` modules.

Routes (all under ``/api/calendar``)::

    GET    /api/calendar/events?hours=48   → upcoming events in the window   → 200 [event, ...]
    GET    /api/calendar/calendars         → configured calendars (masked)   → 200 [calendar, ...]
    POST   /api/calendar/calendars         → add {name, url}                 → 200 [calendar, ...] | 400
    DELETE /api/calendar/calendars/{name}  → remove a calendar               → 200 {removed} | 404
    POST   /api/calendar/primary           → set {name} as the master        → 200 [calendar, ...] | 404
    POST   /api/calendar/sync              → run calendar_sync now           → 200 {summary}

SECURITY: a calendar feed URL is a SECRET (read access to the owner's calendar).
This module NEVER returns a full url in any response; :func:`_mask` renders
only a short, non-reversible tail. ``POST /calendars`` accepts a url from the
client (write-only) but never echoes it back.

The calendar pack mounts this router through ``extensions.register_api_router``.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from loguru import logger

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _mask(url: str) -> str:
    """A calendar url → a short, non-reversible tail. Never the full secret.

    Shows at most the last 6 characters of the url (enough to tell two feeds
    apart at a glance) behind a run of dots; a very short/odd url degrades to
    a fixed placeholder rather than risking a near-full reveal.
    """
    url = str(url or "").strip()
    if len(url) <= 10:
        return "••••••"
    return f"···{url[-6:]}"


def _public_calendars(config=None) -> list[dict]:
    """Every stored calendar, masked: ``{name, url_masked, primary}``. Never raises."""
    try:
        from lhassa.calendar import store as _store

        items = _store.list_calendars(config)
    except Exception as exc:  # defensive; a store hiccup must not 500
        logger.debug("calendar api: list_calendars failed; {}", exc)
        items = []
    return [
        {
            "name": it.get("name") or "",
            "url_masked": _mask(it.get("url") or ""),
            "primary": bool(it.get("primary")),
        }
        for it in items
        if isinstance(it, dict)
    ]


@router.get("/events")
async def get_events(hours: int = 48) -> list[dict]:
    """Upcoming events over ``[now, now+hours]`` across every configured feed.

    ``{summary, start, end, location, calendar, all_day}``: start/end as ISO
    strings. $0, no model. Any feed/parse trouble degrades to ``[]``.
    """
    try:
        hours = max(1, min(int(hours or 48), 24 * 30))
    except (TypeError, ValueError):
        hours = 48
    try:
        from lhassa.calendar import feed as _feed
        from lhassa.config import Config

        events = await _feed.upcoming(Config.instance_id, hours=hours, config=Config)
    except Exception as exc:  # defensive; a feed hiccup must not 500
        logger.debug("calendar api: get_events failed; {}", exc)
        events = []

    out: list[dict] = []
    for ev in events or []:
        try:
            start = ev.get("start")
            end = ev.get("end")
            out.append(
                {
                    "summary": ev.get("summary") or "",
                    "start": start.isoformat() if start is not None else None,
                    "end": end.isoformat() if end is not None else None,
                    "location": ev.get("location") or "",
                    "calendar": ev.get("calendar") or "",
                    "all_day": bool(ev.get("all_day")),
                }
            )
        except Exception:  # a single malformed event must not sink the rest
            continue
    return out


@router.get("/calendars")
async def list_calendars_route() -> list[dict]:
    """Configured calendars; ``{name, url_masked, primary}``. NEVER a full url."""
    return _public_calendars()


@router.post("/calendars")
async def add_calendar_route(body: dict = Body(...)) -> list[dict]:
    """Add (or replace, by name) a calendar feed. ``url`` is write-only; never echoed.

    400 on a missing name/url or a non-http(s) url.
    """
    name = str((body or {}).get("name") or "").strip()
    url = str((body or {}).get("url") or "").strip()
    if not name or not url:
        raise HTTPException(status_code=400, detail="both 'name' and 'url' are required")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="'url' must be an http(s) feed link")

    try:
        from lhassa.calendar import store as _store

        ok = _store.add_calendar(name, url)
    except Exception as exc:  # defensive
        logger.warning("calendar api: add_calendar failed; {}", exc)
        ok = False
    if not ok:
        raise HTTPException(status_code=400, detail="could not save that calendar")
    return _public_calendars()


@router.delete("/calendars/{name}")
async def remove_calendar_route(name: str) -> dict:
    """Remove a stored calendar by name (404 if it doesn't exist)."""
    try:
        from lhassa.calendar import store as _store

        removed = _store.remove_calendar(name)
    except Exception as exc:  # defensive
        logger.warning("calendar api: remove_calendar failed; {}", exc)
        removed = False
    if not removed:
        raise HTTPException(status_code=404, detail=f"calendar {name!r} not found")
    return {"removed": name}


@router.post("/primary")
async def set_primary_route(body: dict = Body(...)) -> list[dict]:
    """Mark a stored calendar PRIMARY; the master :mod:`lhassa.calendar.reconcile`
    diffs the others against. 404 if ``name`` isn't a stored calendar."""
    name = str((body or {}).get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="'name' is required")
    try:
        from lhassa.calendar import store as _store

        ok = _store.set_primary(name)
    except Exception as exc:  # defensive
        logger.warning("calendar api: set_primary failed; {}", exc)
        ok = False
    if not ok:
        raise HTTPException(status_code=404, detail=f"calendar {name!r} not found")
    return _public_calendars()


@router.post("/sync")
async def sync_route() -> dict:
    """Run the ``calendar_sync`` reconcile action now; return its summary string.

    Never raises; :func:`lhassa.calendar.surfaces.actions.calendar_sync` already
    degrades to a clean "nothing to sync" message on any trouble.
    """
    try:
        from lhassa.calendar.surfaces.actions import calendar_sync

        summary = await calendar_sync({})
    except Exception as exc:  # defensive; belt and suspenders
        logger.warning("calendar api: sync failed; {}", exc)
        summary = f"calendar_sync: error: {exc}"
    return {"summary": str(summary)}
