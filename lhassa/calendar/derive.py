"""Calendar-derived world model — bootstrap facts about the owner's world
(courses, recurring commitments, rhythm) FROM the calendar feed the brain
already reads, without asking and without spending: deterministic, $0,
stdlib + :mod:`lhassa.calendar.feed` only, never raises.

Leaf infrastructure, like the rest of ``lhassa.calendar``: this module only
DERIVES candidate facts (a list of dicts) — it never writes to the grande
(see :mod:`lhassa.actionpack._derive` for the shell-side persistence). It
never imports a shell (``tests/test_architecture.py``'s
``calendar-infra-isolation`` rule).

Heuristics — stated HONESTLY, this infers from titles/cadence, not truth:

  * **Recurring commitment / course.** Events are grouped by a normalized
    summary (whitespace-collapsed, casefolded). A group with at least
    :data:`MIN_OCCURRENCES` occurrences whose gaps land in a "weekly-ish"
    band with low variance (the same gaps-between-occurrences
    coefficient-of-variation idea as
    ``lhassa.agents.repetition._cadence_stats``, reimplemented here in pure
    stdlib so this leaf module never imports the adapter-layer ``agents``
    package) becomes a candidate commitment fact. A location that looks like
    a physical room/venue (``sala``, ``bloque``, ``aula``, ``room``,
    ``building``, ``salón``, ``campus``) upgrades the phrasing to "likely a
    course or class"; without that signal it's phrased as a plain recurring
    commitment — we do NOT claim "course" from a title alone.
  * **Meal/break filter.** A small denylist (``almuerzo``, ``lunch``,
    ``break``, ``descanso``, ``café``/``cafe``, ``coffee``, ``desayuno``,
    ``breakfast``, ``merienda``) is skipped outright — a recurring lunch is
    not a course or a commitment worth surfacing as a "fact".
  * **Rhythm.** Weekday + local time-of-day (morning/afternoon/evening) are
    tallied across every non-all-day event in the window; a single rhythm
    fact is derived ONLY when one bucket clearly dominates (see
    :data:`RHYTHM_MIN_EVENTS` / :data:`RHYTHM_MIN_SHARE`) — weak/uniform
    signal yields no rhythm fact at all. This is a summary of busy-ness, not
    a claim about the owner's habits or preferences.
  * **People — SKIPPED on purpose.** :func:`lhassa.calendar.feed.parse_events`
    does not capture ORGANIZER/ATTENDEE fields today, so deriving "meets
    repeatedly with X" would fabricate a signal the parser never gave us.
    Documented here as a future extension, not implemented: if/when the feed
    parser grows attendee support, this module is the natural place to add a
    third derivation (repeated co-attendees → a relationship fact).

Each candidate: ``{"contenido": str, "tags": [...], "procedencia":
"calendar-derived", "confianza": float}``. :func:`derive_world` never
raises — no calendar / no events / any parse hiccup degrades to ``[]``.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime
from typing import Optional

# ── recurring-commitment tuning ──────────────────────────────────────────────

#: A group needs at least this many occurrences before a cadence means
#: anything — matches the design brief's "≥3 occurrences" heuristic.
MIN_OCCURRENCES = 3

#: Coefficient of variation (population stdev / mean of the gaps, in days) at
#: or below this counts as "regular enough". Generous on purpose — real
#: institutional calendars slip (holidays, makeup classes, breaks).
MAX_CV = 0.35

#: A mean gap in this band counts as "weekly-ish" (5-9 days tolerates a
#: slipped week either way without also matching daily standups or monthly
#: meetings).
MIN_INTERVAL_DAYS = 5.0
MAX_INTERVAL_DAYS = 9.0

#: Obvious non-course/non-commitment noise — filtered before grouping even
#: starts, so a recurring lunch never becomes a candidate at all.
_DENYLIST_SUBSTRINGS: tuple[str, ...] = (
    "almuerzo", "lunch", "break", "descanso", "café", "cafe", "coffee",
    "desayuno", "breakfast", "merienda",
)

#: Location substrings that suggest a physical classroom/venue — upgrades a
#: recurring group's phrasing from "commitment" to "course or class".
_ROOM_HINTS: tuple[str, ...] = (
    "sala", "bloque", "aula", "room", "building", "salon", "salón", "campus",
)

# ── rhythm tuning ─────────────────────────────────────────────────────────────

#: Need at least this many timed events in the window before a rhythm claim
#: is worth making at all.
RHYTHM_MIN_EVENTS = 6

#: The dominant weekday+time-of-day bucket must hold at least this share of
#: all timed events, or the calendar is just "busy", not "rhythmic".
RHYTHM_MIN_SHARE = 0.35

_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _normalize_summary(summary: str) -> str:
    return " ".join((summary or "").split()).strip().casefold()


def _is_denylisted(norm_summary: str) -> bool:
    return any(word in norm_summary for word in _DENYLIST_SUBSTRINGS)


def _looks_like_course(locations: list[str]) -> bool:
    for loc in locations:
        low = (loc or "").casefold()
        if any(hint in low for hint in _ROOM_HINTS):
            return True
    return False


def _cadence(starts: list[datetime]) -> Optional[dict]:
    """Gaps-between-occurrences cadence check — same idea as
    ``lhassa.agents.repetition._cadence_stats``, reimplemented in stdlib so
    this leaf module never imports the adapter layer. ``None`` when there
    aren't enough points, the mean gap is off-band, or the gaps are too
    irregular. Never raises.
    """
    try:
        ordered = sorted(starts)
        if len(ordered) < MIN_OCCURRENCES:
            return None
        gaps = [(b - a).total_seconds() / 86400.0 for a, b in zip(ordered, ordered[1:])]
        if not gaps:
            return None
        mean = statistics.mean(gaps)
        if not (MIN_INTERVAL_DAYS <= mean <= MAX_INTERVAL_DAYS):
            return None
        stdev = statistics.pstdev(gaps) if len(gaps) > 1 else 0.0
        cv = stdev / mean if mean else 1.0
        if cv > MAX_CV:
            return None
        return {"interval_days": round(mean, 2), "cv": round(cv, 3), "occurrences": len(ordered)}
    except Exception:
        return None


def _weekday_time_label(dt: datetime) -> tuple[str, str]:
    local = dt.astimezone()
    weekday = _WEEKDAY_NAMES[local.weekday()]
    hour = local.hour
    if hour < 12:
        tod = "morning"
    elif hour < 18:
        tod = "afternoon"
    else:
        tod = "evening"
    return weekday, tod


def _commitment_facts(events: list[dict]) -> list[dict]:
    """Group timed events by normalized summary and emit one candidate fact
    per group with a regular weekly-ish cadence. Filters the meal/break
    denylist BEFORE grouping. Never raises."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        try:
            if ev.get("all_day"):
                continue
            start = ev.get("start")
            summary = ev.get("summary") or ""
            if start is None or not summary.strip():
                continue
            norm = _normalize_summary(summary)
            if not norm or _is_denylisted(norm):
                continue
            groups[norm].append(ev)
        except Exception:
            continue

    facts: list[dict] = []
    for group_events in groups.values():
        try:
            starts = [e["start"] for e in group_events if e.get("start") is not None]
            cadence = _cadence(starts)
            if cadence is None:
                continue
            display = " ".join((group_events[0].get("summary") or "").split()).strip()
            if not display:
                continue
            locations = [e.get("location") or "" for e in group_events]
            first_start = min(starts)
            weekday, _tod = _weekday_time_label(first_start)
            time_str = first_start.astimezone().strftime("%H:%M")
            loc = next((location for location in locations if location), "")
            course_like = _looks_like_course(locations)

            loc_suffix = f", {loc}" if loc else ""
            if course_like:
                contenido = (
                    f"Has a recurring weekly commitment '{display}' "
                    f"({weekday} {time_str}{loc_suffix}) — likely a course or class this term."
                )
                tags = ["calendar", "course"]
            else:
                contenido = (
                    f"Has a recurring weekly commitment '{display}' "
                    f"({weekday} {time_str}{loc_suffix})."
                )
                tags = ["calendar", "commitment"]

            confianza = 0.75 if cadence["cv"] <= 0.15 else 0.6
            facts.append(
                {
                    "contenido": contenido,
                    "tags": tags,
                    "procedencia": "calendar-derived",
                    "confianza": confianza,
                }
            )
        except Exception:
            continue

    facts.sort(key=lambda f: f["contenido"])
    return facts


def _rhythm_fact(events: list[dict]) -> Optional[dict]:
    """One rhythm fact when a single weekday+time-of-day bucket clearly
    dominates the window; ``None`` on weak/uniform/insufficient signal."""
    try:
        timed = [e for e in events if not e.get("all_day") and e.get("start") is not None]
        if len(timed) < RHYTHM_MIN_EVENTS:
            return None
        buckets: dict[tuple[str, str], int] = defaultdict(int)
        for ev in timed:
            buckets[_weekday_time_label(ev["start"])] += 1
        total = len(timed)
        if not buckets or not total:
            return None
        (weekday, tod), count = max(buckets.items(), key=lambda kv: kv[1])
        share = count / total
        if share < RHYTHM_MIN_SHARE:
            return None
        return {
            "contenido": f"Your calendar is busiest on {weekday} {tod}s.",
            "tags": ["calendar", "rhythm"],
            "procedencia": "calendar-derived",
            "confianza": round(min(0.6, 0.3 + share / 2), 2),
        }
    except Exception:
        return None


async def derive_world(instance_id: str, *, config=None, days: int = 45) -> list[dict]:
    """Derive candidate world-model facts from the owner's calendar.

    Reads a wide window (``days`` * 24 hours, recurring events expanded) via
    :func:`lhassa.calendar.feed.upcoming`, then derives recurring-commitment/
    course facts and (best-effort) one rhythm fact. $0, deterministic, no
    model, no network beyond the feed's own cache. Never raises — a missing
    calendar, an empty window, or any internal hiccup degrades to ``[]``.

    People/attendee facts are deliberately NOT derived — see the module
    docstring's "People — SKIPPED on purpose" note.
    """
    try:
        from lhassa.calendar import feed as _feed

        hours = max(24, int(days) * 24)
        events = await _feed.upcoming(instance_id, hours=hours, config=config)
        if not events:
            return []

        candidates: list[dict] = []
        candidates.extend(_commitment_facts(events))
        rhythm = _rhythm_fact(events)
        if rhythm is not None:
            candidates.append(rhythm)
        return candidates
    except Exception:
        return []
