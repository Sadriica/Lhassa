"""Read an institutional calendar's ICS feed — the READ half of the calendar seam.

Leaf infrastructure (pure stdlib + httpx, imports NO shell): fetch a published
``.ics`` feed, cache it, and parse its VEVENTs into a window of aware-UTC events
the personal agenda can merge with tasks. $0, deterministic, no model, never
raises (degrades to ``[]`` / the last cached copy).

The feed URL is a SECRET (read access to the owner's calendar): it comes ONLY
from ``config.calendar_ics_url`` / ``LHASSA_CALENDAR_ICS_URL`` — never hardcoded.
The fetched body is cached under ``~/.lhassa/calendar/`` (private, gitignored).

Institutional reality this is built for (measured against a live Outlook feed):
Windows TZID names like ``SA Pacific Standard Time`` (= America/Bogota, UTC-5,
no DST), a VTIMEZONE block, all-day ``VALUE=DATE`` events, and weekly ``RRULE``
classes. MONTHLY/YEARLY/complex recurrences are best-effort (the base occurrence
only) — a human reads this, and approximate-but-mostly-right beats a heavy dep.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from lhassa.calendar.paths import calendar_dir

# Windows timezone names → fixed UTC offset in hours. Extend as needed; an
# unknown TZID degrades to UTC (logged). Only fixed-offset zones are safe to
# hardcode — all the ones here observe NO DST, so a constant offset is exact.
_WINDOWS_TZ_OFFSETS: dict[str, int] = {
    "SA Pacific Standard Time": -5,      # Bogotá, Lima, Quito (no DST)
    "US Eastern Standard Time": -5,
    "Central Standard Time": -6,
    "Mountain Standard Time": -7,
    "US Mountain Standard Time": -7,     # Arizona (no DST)
    "Pacific Standard Time": -8,
    "Argentina Standard Time": -3,
    "E. South America Standard Time": -3,  # Brasília (no DST since 2019)
    "UTC": 0,
    "GMT Standard Time": 0,
    "W. Europe Standard Time": 1,
    "Romance Standard Time": 1,          # Madrid, Paris
}

_DEFAULT_FEED_TTL = 3600.0  # seconds — /today shouldn't re-download 1MB each call.

# ICS BYDAY codes → Python weekday() (Mon=0).
_BYDAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _cache_path() -> Path:
    return calendar_dir() / "feed_cache.ics"


async def fetch_feed(
    url: str, *, ttl: Optional[float] = None, config=None
) -> Optional[str]:
    """GET the ICS feed, cached by mtime TTL; fall back to the last copy on failure.

    Returns the ICS body, or ``None`` when no URL is given and no cache exists.
    A network error degrades to the cached copy (stale-but-useful) rather than
    nothing. Never raises.
    """
    url = (url or "").strip()
    if not url:
        # No feed configured → no calendar. Never read a cache here: a stray
        # cache (e.g. from a previous configured run) must not leak in.
        return None

    cache = _cache_path()
    if ttl is None:
        ttl = float(getattr(config, "calendar_feed_ttl", _DEFAULT_FEED_TTL) or _DEFAULT_FEED_TTL)

    # Serve a fresh-enough cache without touching the network.
    try:
        if cache.exists() and (time.time() - cache.stat().st_mtime) < ttl:
            return cache.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code == 200 and resp.text:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(resp.text, encoding="utf-8")
            except OSError as exc:
                logger.debug("calendar: could not cache feed — {}", exc)
            return resp.text
        logger.debug("calendar: feed HTTP {}", resp.status_code)
    except Exception as exc:  # a fetch failure falls back to the cache
        logger.debug("calendar: feed fetch failed ({}), using cache", exc)

    try:
        return cache.read_text(encoding="utf-8", errors="replace") if cache.exists() else None
    except OSError:
        return None


def _unfold(ics_text: str) -> list[str]:
    """RFC5545 line unfolding: a line starting with space/tab continues the prior."""
    out: list[str] = []
    for raw in ics_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _unescape(text: str) -> str:
    return (
        text.replace("\\n", "\n").replace("\\N", "\n")
        .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    )


def _parse_dt(value: str, params: dict) -> Optional[datetime]:
    """A DTSTART/DTEND value → aware UTC datetime (or None). Handles TZID / Z / DATE."""
    value = value.strip()
    if not value:
        return None
    # All-day: VALUE=DATE, 8 digits.
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        try:
            d = datetime.strptime(value[:8], "%Y%m%d")
            return d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if value.endswith("Z"):
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        naive = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    tzid = params.get("TZID", "")
    offset = _WINDOWS_TZ_OFFSETS.get(tzid)
    if offset is None:
        if tzid:
            logger.debug("calendar: unknown TZID {!r} — treating as UTC", tzid)
        offset = 0
    # local (naive) in tz(offset) → UTC
    return naive.replace(tzinfo=timezone(timedelta(hours=offset))).astimezone(timezone.utc)


def _parse_rrule(rule: str) -> dict:
    out: dict = {}
    for part in rule.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().upper()] = v.strip()
    return out


def _expand(ev: dict, window_start: datetime, window_end: datetime) -> list[dict]:
    """Occurrences of one VEVENT overlapping the window (RRULE-aware, best-effort)."""
    start, end = ev.get("start"), ev.get("end")
    if start is None:
        return []
    if end is None:
        end = start + timedelta(hours=1)
    dur = end - start
    rrule = ev.get("rrule")

    def _mk(s: datetime) -> dict:
        return {
            "summary": ev["summary"], "location": ev["location"],
            "all_day": ev["all_day"], "start": s, "end": s + dur,
        }

    if not rrule:
        return [_mk(start)] if start < window_end and end > window_start else []

    r = _parse_rrule(rrule)
    freq = r.get("FREQ", "")
    if freq not in ("WEEKLY", "DAILY"):
        # MONTHLY/YEARLY/complex → best-effort: just the base occurrence.
        return [_mk(start)] if start < window_end and end > window_start else []

    interval = max(1, int(r.get("INTERVAL", "1") or 1))
    count = int(r["COUNT"]) if r.get("COUNT", "").isdigit() else None
    until = _parse_dt(r.get("UNTIL", ""), {}) if r.get("UNTIL") else None
    exdates = ev.get("exdates") or set()
    weekdays = (
        {_BYDAY[d] for d in r.get("BYDAY", "").split(",") if d in _BYDAY}
        if freq == "WEEKLY" else None
    )

    occurrences: list[dict] = []
    emitted = 0
    step = timedelta(weeks=interval) if freq == "WEEKLY" else timedelta(days=interval)
    # Anchor to the week/day of DTSTART; walk forward until past the window/until.
    cursor = start
    guard = 0
    while guard < 1000:
        guard += 1
        if until and cursor > until:
            break
        if count is not None and emitted >= count:
            break
        # For WEEKLY+BYDAY, emit each matching weekday in this week span.
        candidates = []
        if freq == "WEEKLY" and weekdays:
            week_monday = cursor - timedelta(days=cursor.weekday())
            for wd in sorted(weekdays):
                candidates.append(
                    week_monday.replace(
                        hour=start.hour, minute=start.minute, second=start.second,
                        microsecond=0,
                    ) + timedelta(days=wd)
                )
        else:
            candidates.append(cursor)
        for occ in candidates:
            if count is not None and emitted >= count:
                break
            if occ < start:
                continue
            if until and occ > until:
                continue
            emitted += 1
            if occ.strftime("%Y%m%dT%H%M%S") in exdates or occ.date().isoformat() in exdates:
                continue
            if occ < window_end and (occ + dur) > window_start:
                occurrences.append(_mk(occ))
        if cursor > window_end:
            break
        cursor += step
    return occurrences


def parse_events(ics_text: str, *, window_start: datetime, window_end: datetime) -> list[dict]:
    """VEVENTs overlapping [window_start, window_end] → sorted aware-UTC events.

    ``{summary, start, end, location, all_day}``. Hand-rolled RFC5545 (unfold,
    KEY;params:value); Windows TZID + Z + all-day; WEEKLY/DAILY RRULE expanded in
    the window (EXDATE honored). Never raises — a malformed feed degrades to ``[]``.
    """
    if not ics_text:
        return []
    try:
        events: list[dict] = []
        cur: Optional[dict] = None
        for line in _unfold(ics_text):
            if line == "BEGIN:VEVENT":
                cur = {"summary": "", "location": "", "all_day": False,
                       "start": None, "end": None, "rrule": None, "exdates": set()}
                continue
            if line == "END:VEVENT":
                if cur is not None:
                    events.extend(_expand(cur, window_start, window_end))
                cur = None
                continue
            if cur is None or ":" not in line:
                continue
            keypart, _, value = line.partition(":")
            bits = keypart.split(";")
            key = bits[0].upper()
            params = {}
            for p in bits[1:]:
                if "=" in p:
                    pk, pv = p.split("=", 1)
                    params[pk.strip().upper()] = pv.strip()
            if key == "SUMMARY":
                cur["summary"] = _unescape(value)[:200]
            elif key == "LOCATION":
                cur["location"] = _unescape(value)[:120]
            elif key == "DTSTART":
                cur["start"] = _parse_dt(value, params)
                cur["all_day"] = params.get("VALUE") == "DATE" or (len(value.strip()) == 8)
            elif key == "DTEND":
                cur["end"] = _parse_dt(value, params)
            elif key == "RRULE":
                cur["rrule"] = value
            elif key == "EXDATE":
                for v in value.split(","):
                    dt = _parse_dt(v, params)
                    if dt is not None:
                        cur["exdates"].add(dt.strftime("%Y%m%dT%H%M%S"))
                        cur["exdates"].add(dt.date().isoformat())
        events.sort(key=lambda e: e["start"])
        return events
    except Exception as exc:  # a parse failure must never break the agenda
        logger.debug("calendar: parse_events failed — {}", exc)
        return []


async def upcoming(instance_id: str, *, hours: int = 36, config=None) -> list[dict]:
    """The owner's real calendar events over [now, now+hours]. $0, never raises.

    The single call the personal agenda uses. Unconfigured feed / any failure →
    ``[]`` (the agenda simply shows tasks only).
    """
    try:
        if config is None:
            from lhassa.config import Config

            config = Config
        url = getattr(config, "calendar_ics_url", "") or ""
        now = datetime.now(timezone.utc)
        ics = await fetch_feed(url, config=config)
        if not ics:
            return []
        return parse_events(ics, window_start=now, window_end=now + timedelta(hours=hours))
    except Exception as exc:
        logger.debug("calendar: upcoming failed — {}", exc)
        return []


def _local_hhmm(dt: datetime) -> str:
    """A UTC event time rendered in the machine's local clock (HH:MM)."""
    return dt.astimezone().strftime("%H:%M")
