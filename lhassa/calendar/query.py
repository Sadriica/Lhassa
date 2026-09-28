# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic calendar-query answering; the READ-in-chat half of the seam.

"Thinking is the EXCEPTION" (§CLAUDE.md): "¿qué hay en mi calendario el
viernes?" / "what do I have Friday?" must be answered from the calendar
DIRECTLY (a date parse + an event lookup + a template reply) never a model
call, never the web-search bridge. This module is that path:

  :func:`parse_window`    : a natural time reference (Spanish + English) → a
                              UTC ``(start, end, label)`` window, or ``None``.
  :func:`is_calendar_query`: is this message asking about the calendar at all?
  :func:`answer`           : glue: classify → parse → :func:`lhassa.calendar.
                              feed.upcoming` → a grouped, deterministic reply.

Leaf infrastructure: stdlib only, plus :mod:`lhassa.calendar.feed` /
:mod:`lhassa.calendar.store` (both leaf themselves); imports NO shell (see
``tests/test_architecture.py``'s ``calendar-infra-isolation`` rule). $0, no
model, no embeddings, no network here directly (``feed.upcoming`` owns that,
and it never raises either). Every public function here is defensive: a
malformed/None input degrades to ``None`` rather than raising, so a caller can
always fall through to the normal chat path.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── weekday vocabulary ───────────────────────────────────────────────────────
# Index = Python's ``datetime.weekday()`` (Monday=0). The "no accents, lower
# case" form is what we match against (see :func:`_normalize`); the display
# form is what a reply actually shows.
_ES_WEEKDAYS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
_ES_WEEKDAYS_DISPLAY = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_EN_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# Labels parse_window can return, used by answer() to pick the reply's language
# and whether to disambiguate with a day-of-month number (weekday labels only).
_ES_PLAIN_LABELS = {
    "hoy", "mañana", "pasado mañana", "esta semana", "este fin de semana",
    "la próxima semana",
}
_EN_PLAIN_LABELS = {
    "today", "tomorrow", "the day after tomorrow", "this week", "this weekend",
    "next week",
}


def _normalize(text: str) -> str:
    """Lowercase, accent-stripped form used only for MATCHING (never displayed)."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.lower()


def _local_midnight(local_now: datetime, offset_days: int = 0) -> datetime:
    """Local-day 00:00, ``offset_days`` from ``local_now``'s date, same tzinfo."""
    d = local_now.date() + timedelta(days=offset_days)
    return datetime(d.year, d.month, d.day, tzinfo=local_now.tzinfo)


def _day_window(local_now: datetime, offset_days: int) -> tuple[datetime, datetime]:
    start = _local_midnight(local_now, offset_days)
    return start, start + timedelta(days=1)


def parse_window(
    text: str, *, now: Optional[datetime] = None
) -> Optional[tuple[datetime, datetime, str]]:
    """Parse a natural time reference (Spanish + English) into a UTC window.

    Returns ``(start_utc, end_utc, label)``: ``label`` is a lowercase phrase in
    whichever language matched (``"el viernes"``, ``"friday"``, ``"hoy"``,
    ``"today"``, ``"esta semana"``, ...), used later to pick the reply's
    language and (for a weekday) whether to show a day-of-month number.

    Windows are LOCAL-day boundaries converted to UTC; "today" means the
    machine's local midnight-to-midnight, not a UTC day. ``now`` defaults to
    the real current time (aware UTC datetime; naive is treated as UTC).
    Recognised references, checked longest/most-specific first so e.g. "pasado
    mañana" never falls through to the plain "mañana" rule:

      pasado mañana / day after tomorrow   → local day +2
      este fin de semana / this weekend    → next Sat 00:00 – next Mon 00:00
      la próxima semana / next week        → next Mon 00:00 – following Mon 00:00
      esta semana / this week              → now → end of THIS week (Sun 24:00)
      hoy / today                          → local day +0
      mañana / tomorrow                    → local day +1
      a weekday name (lunes.../monday...)  → the NEXT occurrence (today counts
                                              if it matches), that single day

    ``None`` when no time reference is found. Never raises.
    """
    try:
        stripped = (text or "").strip()
        if not stripped:
            return None
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        local_now = now.astimezone()
        norm = _normalize(stripped)
        weekday = local_now.weekday()  # Monday=0 .. Sunday=6

        # pasado mañana / day after tomorrow; check BEFORE "mañana"/"tomorrow".
        if re.search(r"\bpasado\s+manana\b", norm):
            start, end = _day_window(local_now, 2)
            return start.astimezone(timezone.utc), end.astimezone(timezone.utc), "pasado mañana"
        if re.search(r"\bday after tomorrow\b", norm):
            start, end = _day_window(local_now, 2)
            return (
                start.astimezone(timezone.utc), end.astimezone(timezone.utc),
                "the day after tomorrow",
            )

        # este fin de semana / this weekend; check BEFORE "esta semana"/"this week".
        if re.search(r"\bfin\s+de\s+semana\b", norm):
            days_to_sat = (5 - weekday) % 7
            start = _local_midnight(local_now, days_to_sat)
            end = start + timedelta(days=2)
            return start.astimezone(timezone.utc), end.astimezone(timezone.utc), "este fin de semana"
        if re.search(r"\bthis\s+weekend\b", norm):
            days_to_sat = (5 - weekday) % 7
            start = _local_midnight(local_now, days_to_sat)
            end = start + timedelta(days=2)
            return start.astimezone(timezone.utc), end.astimezone(timezone.utc), "this weekend"

        # la próxima semana / next week.
        if re.search(r"\b(proxima semana|semana que viene)\b", norm):
            this_monday = _local_midnight(local_now, -weekday)
            start = this_monday + timedelta(days=7)
            end = start + timedelta(days=7)
            return start.astimezone(timezone.utc), end.astimezone(timezone.utc), "la próxima semana"
        if re.search(r"\bnext week\b", norm):
            this_monday = _local_midnight(local_now, -weekday)
            start = this_monday + timedelta(days=7)
            end = start + timedelta(days=7)
            return start.astimezone(timezone.utc), end.astimezone(timezone.utc), "next week"

        # esta semana / this week; now → end of THIS local week (Sunday night).
        if re.search(r"\besta semana\b", norm):
            end = _local_midnight(local_now, (6 - weekday) + 1)
            return now.astimezone(timezone.utc), end.astimezone(timezone.utc), "esta semana"
        if re.search(r"\bthis week\b", norm):
            end = _local_midnight(local_now, (6 - weekday) + 1)
            return now.astimezone(timezone.utc), end.astimezone(timezone.utc), "this week"

        # hoy / today.
        if re.search(r"\bhoy\b", norm):
            start, end = _day_window(local_now, 0)
            return start.astimezone(timezone.utc), end.astimezone(timezone.utc), "hoy"
        if re.search(r"\btoday\b", norm):
            start, end = _day_window(local_now, 0)
            return start.astimezone(timezone.utc), end.astimezone(timezone.utc), "today"

        # mañana / tomorrow.
        if re.search(r"\bmanana\b", norm):
            start, end = _day_window(local_now, 1)
            return start.astimezone(timezone.utc), end.astimezone(timezone.utc), "mañana"
        if re.search(r"\btomorrow\b", norm):
            start, end = _day_window(local_now, 1)
            return start.astimezone(timezone.utc), end.astimezone(timezone.utc), "tomorrow"

        # A weekday name; the NEXT occurrence (today counts if it matches).
        for idx, name in enumerate(_ES_WEEKDAYS):
            if re.search(rf"\b{name}\b", norm):
                days_ahead = (idx - weekday) % 7
                start, end = _day_window(local_now, days_ahead)
                label = f"el {_ES_WEEKDAYS_DISPLAY[idx]}"
                return start.astimezone(timezone.utc), end.astimezone(timezone.utc), label
        for idx, name in enumerate(_EN_WEEKDAYS):
            if re.search(rf"\b{name}\b", norm):
                days_ahead = (idx - weekday) % 7
                start, end = _day_window(local_now, days_ahead)
                return start.astimezone(timezone.utc), end.astimezone(timezone.utc), name

        return None
    except Exception:
        return None


# ── is_calendar_query ────────────────────────────────────────────────────────
#
# Trigger rules (conservative; must NOT swallow normal chat):
#   1. An explicit calendar keyword anywhere in the message: calendario /
#      calendar / agenda / evento(s) / schedule.
#   2. OR a "have/tengo/hay" question WHOSE message also parses a time
#      reference via parse_window(); i.e. both signals must be present. A
#      bare "hay"/"have" with no time reference (e.g. "¿qué hay de nuevo?")
#      is NOT a calendar query; a time reference with no have/tengo/hay word
#      that also isn't a calendar keyword (e.g. "noticias de hoy") is NOT one
#      either. This keeps live-data/news phrasing out.
_CALENDAR_KEYWORD_RE = re.compile(
    r"\b(calendario|calendar|agenda|evento(s)?|events?|schedule)\b", re.IGNORECASE
)
_HAVE_WORD_RE = re.compile(r"\b(have|tengo|hay)\b", re.IGNORECASE)


def is_calendar_query(text: str) -> bool:
    """Is this message asking about the calendar? See module docstring for the
    exact trigger rules. Never raises; a malformed/None input is ``False``."""
    try:
        stripped = (text or "").strip()
    except Exception:
        return False
    if not stripped:
        return False
    if _CALENDAR_KEYWORD_RE.search(stripped):
        return True
    if _HAVE_WORD_RE.search(stripped) and parse_window(stripped) is not None:
        return True
    return False


def _is_spanish_label(label: str) -> bool:
    return label in _ES_PLAIN_LABELS or label.startswith("el ")


def _is_weekday_label(label: str) -> bool:
    return label.startswith("el ") or label in _EN_WEEKDAYS


def _day_phrase(label: str, start_local: datetime, *, has_events: bool, spanish: bool) -> str:
    phrase = label[0].upper() + label[1:]
    if _is_weekday_label(label) and has_events:
        phrase += f" {start_local.day}"
    return phrase


def _event_lines(events: list[dict], *, spanish: bool) -> list[str]:
    """The ``📅 HH:MM Summary · cal · loc`` lines for a list of events.

    Shared by the grouped window reply and the time-of-day reply so both render
    an event identically. Shows the calendar name only when more than one feed
    is in play. Assumes ``events`` is non-empty and pre-sorted by start.
    """
    lines: list[str] = []
    multi_cal = len({e.get("calendar") for e in events if e.get("calendar")}) > 1
    for ev in events:
        from lhassa.calendar.feed import _local_hhmm

        if ev.get("all_day"):
            when = "Todo el día" if spanish else "All day"
        else:
            when = _local_hhmm(ev["start"])
        cal = f" · {ev['calendar']}" if multi_cal and ev.get("calendar") else ""
        loc = f" · {ev['location']}" if ev.get("location") else ""
        lines.append(f"📅 {when} {ev.get('summary', '')}{cal}{loc}")
    return lines


def _format_reply(
    events: list[dict], *, label: str, start_utc: datetime
) -> str:
    """Render the grouped, deterministic reply for a resolved window's events."""
    spanish = _is_spanish_label(label)
    start_local = start_utc.astimezone()
    phrase = _day_phrase(label, start_local, has_events=bool(events), spanish=spanish)

    if not events:
        if spanish:
            return f"{phrase} no tienes nada en tu calendario."
        return f"{phrase} you don't have anything on your calendar."

    n = len(events)
    if spanish:
        noun = "cosa" if n == 1 else "cosas"
        lines = [f"{phrase} tienes {n} {noun}:"]
    else:
        noun = "thing" if n == 1 else "things"
        lines = [f"{phrase} you have {n} {noun}:"]

    lines.extend(_event_lines(events, spanish=spanish))
    return "\n".join(lines)


async def answer(instance_id: str, text: str, *, config=None) -> Optional[str]:
    """Answer a calendar question DETERMINISTICALLY, $0, no model.

    ``None`` when the message isn't a calendar query (or has no parseable time
    reference); the caller should fall through to the normal chat path in
    that case. Gates on :func:`is_calendar_query` then delegates the actual
    window resolution to :func:`answer_window` (shared with the elliptical
    follow-up continuation path; see :mod:`lhassa.notify.capabilities`).
    Never raises; any trouble degrades to ``None`` (fall through).
    """
    try:
        if not is_calendar_query(text):
            return None
        return await answer_window(instance_id, text, config=config)
    except Exception:
        return None


async def answer_window(instance_id: str, text: str, *, config=None) -> Optional[str]:
    """Answer straight from ``text``'s own parsed time window; deterministic,
    $0. WITHOUT the :func:`is_calendar_query` keyword/have-word gate.

    Factored out of :func:`answer` for the sticky-topic follow-up continuation:
    an elliptical "Y el sábado?" carries no calendar keyword and no have/
    tengo/hay word, so ``is_calendar_query`` alone would refuse it; but once
    the conversation's STICKY topic is already ``calendar_view``, the
    follow-up's own time reference (parsed here) is enough. Fetches events via
    :func:`lhassa.calendar.feed.upcoming` (a real network/cache read, but
    itself $0/deterministic/never-raising) over a window wide enough to cover
    the parsed range, filters to ``[start, end)``, and renders a grouped
    reply. ``None`` when ``text`` has no parseable time reference (or the feed
    lookup fails); the caller then falls through to its normal path. Never
    raises.
    """
    try:
        window = parse_window(text)
        if window is None:
            return None
        start_utc, end_utc, label = window

        from lhassa.calendar import feed as _feed

        now = datetime.now(timezone.utc)
        span_hours = (end_utc - now).total_seconds() / 3600.0
        hours = max(24, int(span_hours) + 6)  # a little slack past the window edge

        # Reach BACK to the window's own start so a single-day query ("hoy")
        # includes events that already happened today; otherwise "hoy" asked
        # mid-morning silently drops the 08:00 class and then flatly denies it
        # exists when the owner follows up "¿y a las 8?".
        events = await _feed.upcoming(
            instance_id, hours=hours, since=min(start_utc, now), config=config
        )
        filtered = [
            e for e in events
            if e.get("start") is not None and start_utc <= e["start"] < end_utc
        ]
        filtered.sort(key=lambda e: e["start"])
        return _format_reply(filtered, label=label, start_utc=start_utc)
    except Exception:
        return None


# ── time-of-day follow-ups ("¿y a las 6am?") ─────────────────────────────────
#
# A day window (parse_window) answers "what do I have today?". Its natural
# elliptical follow-up is a bare TIME; "¿y a las 6am?", "and at 5pm?": which
# carries NO day reference, so parse_window declines and the message used to
# fall to a tool-less think that hallucinated "no hay nada". These two helpers
# resolve the time against the conversation's sticky calendar DAY instead.
#
# Only fires as a follow-up (see lhassa.notify.capabilities): the sticky topic
# is already calendar_view, and this runs AFTER answer_window declined. It
# demands an explicit time signal; a meridiem (am/pm), a colon (6:30), or an
# "a las"/"at" lead; so a bare number in ordinary chat ("sala 6") never trips
# it. A bare 1–12 with no meridiem is AMBIGUOUS, so both readings are checked
# (6 → 06:00 AND 18:00): whichever the calendar actually has wins.
_TOD_RE = re.compile(
    r"(?:\b(a\s+las?|at)\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?)?",
)


def parse_time_of_day(text: str) -> Optional[list[tuple[int, int]]]:
    """A bare time reference → candidate ``(hour24, minute)`` tuples, or ``None``.

    Returns EVERY plausible 24h reading: an unambiguous one (meridiem or 24h
    value → a single tuple), or both AM and PM for a bare 1–12. ``None`` when
    there is no time, or no explicit time signal (meridiem / colon / "a las" /
    "at"); so ordinary chat never matches. Never raises.
    """
    try:
        norm = _normalize(text or "")
        m = _TOD_RE.search(norm)
        if not m:
            return None
        lead, hh, mm, mer = m.group(1), m.group(2), m.group(3), m.group(4)
        mer = (mer or "").replace(".", "").replace(" ", "")
        # Require an explicit time signal; a lone number is not a time.
        if not (lead or mer or mm):
            return None
        hour = int(hh)
        minute = int(mm) if mm else 0
        if hour > 23 or minute > 59:
            return None
        if mer == "am":
            return [(0 if hour == 12 else hour, minute)]
        if mer == "pm":
            return [(hour if hour == 12 else (hour + 12) % 24, minute)]
        # No meridiem: a 0 or 13–23 value is already unambiguous 24h.
        if hour == 0 or hour > 12:
            return [(hour, minute)]
        # Ambiguous 1–12 → check both readings; the calendar disambiguates.
        cands = {(hour, minute), ((hour + 12) % 24, minute)}
        return sorted(cands)
    except Exception:
        return None


def resolved_date(text: str, *, now: Optional[datetime] = None) -> Optional[str]:
    """The local ISO date a calendar message's window starts on, or ``None``.

    Used by the chat layer to remember WHICH day the sticky calendar topic is
    on, so a later bare-time follow-up ("¿y a las 6am?") resolves against the
    right day (today after "hoy", tomorrow after "mañana"). Never raises.
    """
    try:
        window = parse_window(text, now=now)
        if window is None:
            return None
        start_utc, _end, _label = window
        return start_utc.astimezone().date().isoformat()
    except Exception:
        return None


def _is_spanish_tod(text: str) -> bool:
    """Language of a time-of-day follow-up. Spanish unless it clearly reads
    English ("at 6pm" with no Spanish "las"). Defaults Spanish (the owner)."""
    norm = _normalize(text)
    if re.search(r"\blas\b", norm):
        return True
    if re.search(r"\bat\b", norm):
        return False
    return True


async def answer_time_of_day(
    instance_id: str, text: str, *, day_iso: Optional[str] = None, config=None
) -> Optional[str]:
    """Answer a bare-time follow-up against a specific DAY; deterministic, $0.

    Resolves ``text``'s time (:func:`parse_time_of_day`) against ``day_iso``
    (the sticky calendar day; defaults to today), fetches that FULL local day's
    events (reaching back past ``now`` so a morning slot still shows) and
    reports the events at/around that time. ``None`` when ``text`` carries no
    parseable time; the caller falls through to its normal path. A day with
    nothing at that time answers honestly ("no tienes nada a las …") rather
    than falling through to a model that would guess. Never raises.
    """
    try:
        cands = parse_time_of_day(text)
        if not cands:
            return None

        local_now = datetime.now(timezone.utc).astimezone()
        target_date = None
        if day_iso:
            try:
                target_date = datetime.fromisoformat(day_iso).date()
            except (ValueError, TypeError):
                target_date = None
        if target_date is None:
            target_date = local_now.date()

        tz = local_now.tzinfo
        day_start_local = datetime(
            target_date.year, target_date.month, target_date.day, tzinfo=tz
        )
        day_start_utc = day_start_local.astimezone(timezone.utc)
        day_end_utc = (day_start_local + timedelta(days=1)).astimezone(timezone.utc)

        from lhassa.calendar import feed as _feed

        now = datetime.now(timezone.utc)
        span_hours = (day_end_utc - now).total_seconds() / 3600.0
        hours = max(24, int(span_hours) + 6)
        events = await _feed.upcoming(
            instance_id, hours=hours, since=min(day_start_utc, now), config=config
        )
        day_events = [
            e for e in events
            if e.get("start") is not None and day_start_utc <= e["start"] < day_end_utc
        ]

        matched: list[dict] = []
        for ev in day_events:
            start_local = ev["start"].astimezone()
            end_local = (ev.get("end") or ev["start"]).astimezone()
            for hour, minute in cands:
                target = day_start_local.replace(hour=hour, minute=minute)
                covers = start_local <= target < end_local
                starts_at = start_local.hour == hour and (
                    minute == 0 or start_local.minute == minute
                )
                if covers or starts_at:
                    matched.append(ev)
                    break
        matched.sort(key=lambda e: e["start"])

        spanish = _is_spanish_tod(text)
        hour0, minute0 = cands[0]
        tlabel = f"{hour0:02d}:{minute0:02d}"
        if not matched:
            if spanish:
                return f"No tienes nada a las {tlabel}."
            return f"You don't have anything at {tlabel}."

        header = f"A las {tlabel} tienes:" if spanish else f"At {tlabel} you have:"
        return "\n".join([header, *_event_lines(matched, spanish=spanish)])
    except Exception:
        return None
