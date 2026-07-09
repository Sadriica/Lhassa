"""Master-calendar reconcile — the $0, deterministic diff engine.

An owner with several calendars (institutional, personal, a project one) picks
ONE as the PRIMARY (the master — see :func:`lhassa.calendar.store.set_primary`).
This module finds events that live on a NON-primary ("secondary") calendar but
are missing from the primary, so they can be surfaced to the owner as a
tappable ``.ics`` to add — never modifying any calendar directly (there's no
write API to an institutional feed; a file the owner taps is the integration).

Set diff, not sync: this reads :func:`lhassa.calendar.feed.upcoming` (which
merges every configured feed and tags each event with its ``calendar`` name),
partitions by the primary calendar, then matches each secondary event against
the primary set. No model, no network call of its own (``upcoming`` does the
one HTTP round trip per feed, already cached) — everything downstream is a
plain in-memory diff.

Matching heuristic (and its honest limits)
-------------------------------------------
Two events are considered "the same" when:

  1. their ``summary`` matches after :func:`_normalize` (casefold + collapse
     internal whitespace to single spaces + strip) — so "Team  meeting" and
     "team meeting" are the same event; and
  2. their ``start`` times are within :data:`MATCH_TOLERANCE_MINUTES` of each
     other (default 5 minutes) — so a feed that rounds to the minute or a
     institutional export off by a couple of minutes still matches.

This is intentionally simple and has known false negatives: a RENAMED event
(same time, different title — e.g. "Team meeting" → "Team sync") reads as
unsynced even though a human would recognize it as the same meeting, and an
event moved by more than the tolerance also reads as unsynced. Both are
ACCEPTABLE by design — the owner reviews and taps each candidate rather than
this module silently guessing; a false "unsynced" costs one ignored
notification, while a false match (silently treating two different events as
the same) would hide a real gap. There are no false POSITIVES: a genuinely
missing event is always surfaced.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from loguru import logger

# How close two start times must be to count as "the same" event.
MATCH_TOLERANCE_MINUTES = 5

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(summary: str) -> str:
    """Casefold + collapse whitespace — the only text the matcher looks at."""
    return _WHITESPACE_RE.sub(" ", str(summary or "")).strip().casefold()


def _matches(candidate: dict, primary_events: list[dict]) -> bool:
    """True iff some primary event has the same normalized summary AND a start
    time within :data:`MATCH_TOLERANCE_MINUTES` of ``candidate``'s."""
    tolerance = timedelta(minutes=MATCH_TOLERANCE_MINUTES)
    cand_summary = _normalize(candidate.get("summary"))
    cand_start = candidate.get("start")
    if cand_start is None:
        return False
    for ev in primary_events:
        if _normalize(ev.get("summary")) != cand_summary:
            continue
        ev_start = ev.get("start")
        if ev_start is None:
            continue
        if abs(ev_start - cand_start) <= tolerance:
            return True
    return False


async def find_unsynced(
    instance_id: str, *, hours: int = 168, config=None
) -> list[dict]:
    """Secondary-calendar events with no matching event on the primary.

    Fetches :func:`lhassa.calendar.feed.upcoming` over ``[now, now+hours]``
    (default a week), partitions by :func:`lhassa.calendar.store.get_primary`,
    and returns every non-primary event that doesn't :func:`_matches` some
    primary event. Each candidate: ``{summary, start, end, location,
    source_calendar}``.

    No primary configured, or no feeds at all, degrades to ``[]`` — there is
    nothing to reconcile against. Never raises.
    """
    try:
        from lhassa.calendar import feed as _feed
        from lhassa.calendar import store as _store

        primary_name = _store.get_primary(config)
        if not primary_name:
            return []

        events = await _feed.upcoming(instance_id, hours=hours, config=config)
        if not events:
            return []

        primary_events = [e for e in events if e.get("calendar") == primary_name]
        secondary_events = [e for e in events if e.get("calendar") != primary_name]

        candidates: list[dict] = []
        for ev in secondary_events:
            if _matches(ev, primary_events):
                continue
            candidates.append(
                {
                    "summary": ev.get("summary") or "",
                    "start": ev.get("start"),
                    "end": ev.get("end"),
                    "location": ev.get("location") or "",
                    "source_calendar": ev.get("calendar") or "",
                }
            )
        return candidates
    except Exception as exc:  # $0 diff must never crash a caller
        logger.debug("calendar.reconcile: find_unsynced failed — {}", exc)
        return []


def to_ics(candidate: dict) -> str:
    """A ``.ics`` string for ``candidate`` — a stable UID from ``summary`` +
    ``start`` so the SAME unsynced event always yields the SAME file content
    (upstream dedupe keys on this, see :mod:`lhassa.actionpack._calendar_sync`).

    Delegates the actual RFC5545 rendering to
    :func:`lhassa.calendar.ics.build_event` — this function only shapes a
    reconcile candidate into that call. Never raises; ``""`` on any trouble
    (e.g. a candidate missing ``start``), matching ``build_event``'s own
    degrade-to-empty-string contract.
    """
    try:
        from lhassa.calendar.ics import build_event

        start = candidate.get("start")
        if start is None:
            return ""
        summary = candidate.get("summary") or "Event"
        end = candidate.get("end")
        location = candidate.get("location") or ""
        source = candidate.get("source_calendar") or ""
        description = f"From {source}" if source else ""
        uid = stable_uid(summary, start)
        return build_event(
            summary,
            start,
            end_dt=end,
            description=description,
            location=location,
            uid=uid,
        )
    except Exception as exc:
        logger.warning("calendar.reconcile: to_ics failed — {}", exc)
        return ""


def stable_uid(summary: str, start: datetime) -> str:
    """Deterministic UID seed — same summary+start always produces the same
    UID, which is what makes ``calendar_sync``'s watch_state dedupe work."""
    seed = f"{_normalize(summary)}|{start.isoformat()}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]
    return f"reconcile-{digest}@lhassa"


__all__ = ["MATCH_TOLERANCE_MINUTES", "find_unsynced", "stable_uid", "to_ics"]
