"""Deterministic calendar-query answering — the READ-in-chat half of the seam.

"Thinking is the EXCEPTION" (§CLAUDE.md): "¿qué hay en mi calendario el
viernes?" / "what do I have Friday?" must be answered from the calendar
DIRECTLY — a date parse + an event lookup + a template reply — never a model
call, never the web-search bridge. This module is that path:

  :func:`parse_window`     — a natural time reference (Spanish + English) → a
                              UTC ``(start, end, label)`` window, or ``None``.
  :func:`is_calendar_query` — is this message asking about the calendar at all?
  :func:`answer`            — glue: classify → parse → :func:`lhassa.calendar.
                              feed.upcoming` → a grouped, deterministic reply.

Leaf infrastructure: stdlib only, plus :mod:`lhassa.calendar.feed` /
:mod:`lhassa.calendar.store` (both leaf themselves) — imports NO shell (see
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

    Returns ``(start_utc, end_utc, label)`` — ``label`` is a lowercase phrase in
    whichever language matched (``"el viernes"``, ``"friday"``, ``"hoy"``,
    ``"today"``, ``"esta semana"``, ...), used later to pick the reply's
    language and (for a weekday) whether to show a day-of-month number.

    Windows are LOCAL-day boundaries converted to UTC — "today" means the
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

        # pasado mañana / day after tomorrow — check BEFORE "mañana"/"tomorrow".
        if re.search(r"\bpasado\s+manana\b", norm):
            start, end = _day_window(local_now, 2)
            return start.astimezone(timezone.utc), end.astimezone(timezone.utc), "pasado mañana"
        if re.search(r"\bday after tomorrow\b", norm):
            start, end = _day_window(local_now, 2)
            return (
                start.astimezone(timezone.utc), end.astimezone(timezone.utc),
                "the day after tomorrow",
            )

        # este fin de semana / this weekend — check BEFORE "esta semana"/"this week".
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

        # esta semana / this week — now → end of THIS local week (Sunday night).
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

        # A weekday name — the NEXT occurrence (today counts if it matches).
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
# Trigger rules (conservative — must NOT swallow normal chat):
#   1. An explicit calendar keyword anywhere in the message: calendario /
#      calendar / agenda / evento(s) / schedule.
#   2. OR a "have/tengo/hay" question WHOSE message also parses a time
#      reference via parse_window() — i.e. both signals must be present. A
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
    exact trigger rules. Never raises — a malformed/None input is ``False``."""
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

    return "\n".join(lines)


async def answer(instance_id: str, text: str, *, config=None) -> Optional[str]:
    """Answer a calendar question DETERMINISTICALLY, $0, no model.

    ``None`` when the message isn't a calendar query (or has no parseable time
    reference) — the caller should fall through to the normal chat path in
    that case. Otherwise fetches events via
    :func:`lhassa.calendar.feed.upcoming` (a real network/cache read, but
    itself $0/deterministic/never-raising) over a window wide enough to cover
    the parsed range, filters to ``[start, end)``, and renders a grouped
    reply. Never raises — any trouble degrades to ``None`` (fall through).
    """
    try:
        if not is_calendar_query(text):
            return None
        window = parse_window(text)
        if window is None:
            return None
        start_utc, end_utc, label = window

        from lhassa.calendar import feed as _feed

        now = datetime.now(timezone.utc)
        span_hours = (end_utc - now).total_seconds() / 3600.0
        hours = max(24, int(span_hours) + 6)  # a little slack past the window edge

        events = await _feed.upcoming(instance_id, hours=hours, config=config)
        filtered = [
            e for e in events
            if e.get("start") is not None and start_utc <= e["start"] < end_utc
        ]
        filtered.sort(key=lambda e: e["start"])
        return _format_reply(filtered, label=label, start_utc=start_utc)
    except Exception:
        return None
