# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Repeated-think detector; the $0 sense that spots automatable patterns (§2.8).

The self-improvement pipeline (quarantine -> gates -> versioning) has always had
a missing FIRST step: nothing ever noticed that the brain was judging (spending
tokens) on essentially the same thing over and over. This module is that sense.

Detection is a PURE SQL aggregation over the episodes table (the append-only
grande): group recent episodes by (despertar_por, accion), count occurrences and
sum cost. A pattern that repeats often AND cost real money is a strong signal a
deterministic action could replace the judgment entirely. This never calls an
LLM and never writes anything; see :mod:`lhassa.actionpack._selfimprove` for the
action that turns a finding into a parked approval + notification.

**A judgment is never a valid candidate.** The detector's first five real
proposals were "make 'responder' automatic" (the Telegram chat turn itself) and
four "make 'dept:<x> think' automatic" (an org department's judgment); the
owner denied all five, because neither can EVER be a deterministic action: a
chat reply and a department's "think" are, by definition, the judgment the
self-improvement loop exists to relieve, not something downstream of it.
:func:`is_excluded` is the explicit predicate that keeps these out of every
finding this module returns.

This module also finds a second, complementary signal: repeated MECHANICAL
ACTION CHAINS (:func:`detect_mechanical_chains`); an ordered sequence of ≥2
distinct deterministic actions recurring under the same trigger. That is not a
"mint one action" candidate; it is a Programmatic Tool Calling (PTC, see
:mod:`lhassa.agents.ptc`) candidate; collapse the chain into one script a
human reviews once instead of N separate fixed-action episodes.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from lhassa._vendor import aiosqlite
from lhassa.config import Config
from lhassa.core import db as _db

# Default lookback window and repeat count, overridable per call or via
# Config.repetition_threshold.
DEFAULT_DAYS = 7
DEFAULT_THRESHOLD = 5

# Mechanical-chain detection defaults (see detect_mechanical_chains).
DEFAULT_CHAIN_THRESHOLD = 3
DEFAULT_CHAIN_MAX_LEN = 4

# detect_recurring defaults; a much lower bar than detect_repetition's cost
# threshold: a pattern is worth noticing just for repeating, regardless of cost.
DEFAULT_RECURRING_THRESHOLD = 3
# A cadence needs at least this many occurrences before gaps mean anything.
CADENCE_MIN_OCCURRENCES = 3
# Coefficient of variation (stdev/mean of the gaps, in days) at or below this
# is treated as a "regular" cadence; a HEURISTIC, not exact schedule
# detection: a human still reviews the resulting schedule proposal.
CADENCE_MAX_CV = 0.35
# A mean gap below this (in days) is just "close together in a burst", not a
# cadence worth scheduling; floors out sub-day noise (e.g. a rapid retry loop).
CADENCE_MIN_INTERVAL_DAYS = 0.5

# The floor every adaptive threshold relaxes toward (see effective_threshold);
# a single or double occurrence is still coincidence, never a "pattern" worth
# surfacing, no matter how small the window's population is.
ADAPTIVE_THRESHOLD_FLOOR = 2
# tipos the denied-patterns query recognizes; every self-improvement proposal
# kind this module (and actionpack._selfimprove) can park an approval for.
_PROPOSAL_TIPOS = (
    "mint_action",
    "mint_script",
    "mint_memory",
    "mint_schedule",
    "seed_cache",
    "mint_route",
)

# detect_routable defaults; how many times a `[routable:<cap>]`-tagged episode
# (a message a deterministic capability recognises, that STILL paid for a
# think/research) must repeat before it is worth proposing a route.
DEFAULT_ROUTABLE_THRESHOLD = 5

# The exact marker lhassa.notify.capabilities.tag_intent writes onto an
# episode's `decision` text; read back here with a plain regex, never a
# Python import of notify/ (agents/ stays shell-free; see the module docstring
# and lhassa.notify.capabilities's own docstring for the layering rationale).
_ROUTABLE_RE = re.compile(r"\[routable:([a-z0-9_]+)\]", re.IGNORECASE)

# Candidate action names that are ALWAYS a judgment, never automatable; see the
# module docstring for the real denied proposals this encodes.
_EXCLUDED_ACCIONES = {"responder"}
# An org department's judgment think, e.g. "dept:interns think".
_DEPT_THINK_RE = re.compile(r"^dept:.+ think$")

# ── infrastructure (Lhassa's OWN machinery) ──────────────────────────────────
#
# The self-improvement loop exists to find repeated OWNER-facing work worth
# automating. It must NOT find Lhassa's own deterministic scheduled/org
# machinery: the org delegation+delivery hops and the maintenance-seed beats
# fire every heartbeat, always $0, and "optimizing" them is the snake eating
# its own tail; the chain detector would even propose collapsing chains that
# CONTAIN detect_repetition itself. Left unfiltered this floods the owner with
# near-duplicate mint_script proposals (every rotation of the same 3-4 hygiene
# actions is a distinct "chain"). Keyed on despertar_por: every real infra
# episode carries an infra wake (an org hop, the monitor, or a scheduled
# maintenance `task:<action>`). Owner-driven wakes; telegram, a
# reminder/research/goal/org_run task; are deliberately absent, so genuine
# repeated owner work still surfaces.
_INFRA_WAKE = {
    "org-deliver", "org-delegate", "temple-execute", "templo-execute", "monitor",
}
_INFRA_TASK_WAKES = {
    "task:temple_run", "task:distill_memory", "task:detect_repetition",
    "task:review_tools", "task:curate_skills", "task:calendar_sync",
    "task:consolidate_memory", "task:send_digest", "task:sync_repo",
}


def is_infrastructure(despertar_por: str, accion: str = "") -> bool:
    """True when an episode is Lhassa's OWN scheduled/org machinery; never an
    intervention target (see the block comment above). Keyed on the wake source;
    ``accion`` is accepted for signature parity but not required. Never raises."""
    dp = (despertar_por or "").strip()
    return dp in _INFRA_WAKE or dp in _INFRA_TASK_WAKES


def pattern_key(despertar_por: str, accion: str) -> str:
    """Normalized grouping key for one repeated-think pattern."""
    return f"{(despertar_por or '').strip()}::{(accion or '').strip()}"


def effective_threshold(
    base: int, population: int, *, floor: int = ADAPTIVE_THRESHOLD_FLOOR
) -> int:
    """The adaptive repeat threshold; sensitivity that SCALES with data volume.

    A YOUNG brain (few episodes total) has no chance of ever hitting a static
    ``base`` threshold (e.g. 5 repeats) inside a 7-day window; a MATURE brain
    with thousands of episodes should keep the strict ``base`` bar so it isn't
    flooded with noise. This is the one rule every detector in this module
    applies to bridge the two:

        threshold = max(floor, min(base, ceil(population / base)))

    Read as: treat ``base`` as this pattern's own natural denominator. The
    threshold only reaches its full, strict ``base`` value once ``population``
    is at least ``base`` groups' worth of ``base`` occurrences each; i.e.
    ``population >= base * base``. Below that the window is sparse/young and
    the bar relaxes proportionally (``ceil(population / base)``), down to
    ``floor``. ``floor`` guards the other end: the threshold never drops below
    it, because a single or double occurrence is still coincidence, not a
    pattern (mirrors :data:`CADENCE_MIN_OCCURRENCES`'s own floor of 2 for
    cadence detection; a cadence needs at least 2 gaps to mean anything).

    Monotonic (non-decreasing) in ``population`` by construction: ``ceil`` is
    non-decreasing in its argument, and clamping with fixed constants
    (``min``/``max``) preserves monotonicity; a bigger window NEVER makes
    detection stricter than a smaller one.

    Defensive, never raises: a non-numeric/zero/negative ``base`` degrades to
    ``1``; ``floor`` is clamped to ``[1, base]`` so it can never exceed the
    value it is supposed to relax toward; a non-numeric/negative ``population``
    degrades to ``0``.
    """
    try:
        base = int(base)
    except (TypeError, ValueError):
        base = 1
    if base < 1:
        base = 1
    try:
        floor = int(floor)
    except (TypeError, ValueError):
        floor = 1
    floor = max(1, min(floor, base))
    try:
        population = int(population)
    except (TypeError, ValueError):
        population = 0
    if population < 0:
        population = 0
    scaled = math.ceil(population / base)
    return max(floor, min(base, scaled))


def chain_pattern_key(despertar_por: str, chain: list[str]) -> str:
    """Normalized grouping key for one repeated mechanical action chain."""
    return f"{(despertar_por or '').strip()}::chain:{'→'.join(chain)}"


def is_excluded(accion: str, decision: str = "") -> bool:
    """True when a pattern can NEVER become a deterministic action; never propose it.

    A judgment (a chat reply, an org department's "think") cannot be minted as a
    $0 fixed action by definition: doing so would just hide an LLM call behind a
    fake deterministic name, defeating the point of the self-improvement loop.
    Excludes any pattern whose:

      - candidate ``accion`` is exactly ``"responder"``: the tool-less
        Telegram/chat think (see ``chat.think``'s fallback reply).
      - candidate ``accion`` matches ``"dept:<name> think"``: an org
        department's judgment (see ``org.coordinator``/``org.executor``).
      - recorded ``decision`` text starts with ``"think"``: a second, more
        general net that catches any other judgment shape that doesn't happen
        to be spelled "responder" or "dept:* think" (e.g. a future chat/org
        think renamed or phrased differently).
    """
    accion = (accion or "").strip()
    decision = (decision or "").strip().lower()
    if accion in _EXCLUDED_ACCIONES:
        return True
    if _DEPT_THINK_RE.match(accion):
        return True
    if decision.startswith("think"):
        return True
    return False


async def denied_patterns(instance_id: str) -> set[str]:
    """Patterns whose most recent self-improvement proposal was DENIED.

    Covers every proposal kind this module (and ``actionpack._selfimprove``) can
    park; ``mint_action``, ``mint_script``, and the consciousness's ``mint_memory``
    / ``mint_schedule`` / ``seed_cache`` kinds (see :func:`recommend_intervention`).

    ``watch_state`` (see ``actionpack._selfimprove._SEEN_PREFIX``) only stops a
    pattern from being re-proposed while its approval is still PENDING; once a
    human resolves it ``rechazada`` it falls out of that dedup path entirely, and
    the pattern would otherwise be proposed again on the very next run. A denial
    is a human decision, not a queue timeout; this reads the approvals table
    directly (``core.db`` exposes no "list denied" helper) so the owner is never
    asked twice for an automation they already said no to. Pure SQL, $0, never
    raises (degrades to an empty set).
    """
    patterns: set[str] = set()
    try:
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            placeholders = ",".join("?" * len(_PROPOSAL_TIPOS))
            async with conn.execute(
                f"""SELECT payload FROM approvals
                   WHERE instance_id = ? AND estado = 'rechazada'
                     AND tipo IN ({placeholders})""",
                (instance_id, *_PROPOSAL_TIPOS),
            ) as cur:
                rows = await cur.fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (ValueError, TypeError):
                continue
            pat = (payload or {}).get("pattern")
            if pat:
                patterns.add(str(pat))
    except Exception:
        return set()
    return patterns


async def detect_repetition(
    instance_id: str,
    *,
    days: int = DEFAULT_DAYS,
    threshold: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Find (despertar_por, accion) patterns repeated >= an effective threshold.

    Pure SQL aggregation over the last ``days`` of episodes for ``instance_id``;
    no LLM, no cost, never raises destructively. Only patterns that both repeat
    enough times AND have spent real money (``total_cost_usd > 0``) are
    returned; a pattern that already costs nothing is not worth automating
    further. Sorted by repeat count descending.

    An explicit ``threshold`` (or ``Config.repetition_threshold``, when set)
    ALWAYS wins unchanged. Otherwise the base (:data:`DEFAULT_THRESHOLD`)
    adapts to the window's population; see :func:`effective_threshold`: so a
    young brain with few episodes still gets real findings instead of "no
    repeated patterns found" forever.

    Each item: ``{"pattern", "despertar_por", "accion", "count",
    "total_cost_usd", "threshold_used"}``.
    """
    explicit = threshold is not None
    base_threshold = threshold if explicit else int(Config.repetition_threshold)
    if base_threshold < 1:
        base_threshold = DEFAULT_THRESHOLD
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """SELECT COUNT(*) AS n FROM episodes
                   WHERE instance_id = ? AND creado_en >= ?""",
                (instance_id, cutoff),
            ) as pcur:
                prow = await pcur.fetchone()
            population = int((prow["n"] if prow else 0) or 0)
            used_threshold = (
                base_threshold
                if explicit
                else effective_threshold(base_threshold, population)
            )
            async with conn.execute(
                """SELECT despertar_por, accion,
                          COUNT(*) AS n,
                          SUM(COALESCE(costo_usd, 0)) AS cost,
                          MAX(decision) AS decision_sample
                   FROM episodes
                   WHERE instance_id = ? AND creado_en >= ?
                   GROUP BY despertar_por, accion
                   HAVING COUNT(*) >= ? AND SUM(COALESCE(costo_usd, 0)) > 0
                   ORDER BY n DESC""",
                (instance_id, cutoff, used_threshold),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
    except Exception:
        # A missing/corrupt table degrades to "nothing detected": this is a
        # cheap sense, not a critical path; it must never crash a caller.
        return []

    denied = await denied_patterns(instance_id)
    out: list[dict[str, Any]] = []
    for row in rows:
        despertar_por = row.get("despertar_por") or ""
        accion = row.get("accion") or ""
        if is_excluded(accion, str(row.get("decision_sample") or "")):
            continue  # a judgment can never become a deterministic action
        if is_infrastructure(despertar_por, accion):
            continue  # Lhassa's own scheduled/org machinery; not owner work
        pattern = pattern_key(despertar_por, accion)
        if pattern in denied:
            continue  # the owner already said no to this exact automation
        out.append(
            {
                "pattern": pattern,
                "despertar_por": despertar_por,
                "accion": accion,
                "count": int(row.get("n") or 0),
                "total_cost_usd": float(row.get("cost") or 0.0),
                "threshold_used": used_threshold,
            }
        )
    return out


def _cadence_stats(
    timestamps: list[str], *, min_occurrences: int = CADENCE_MIN_OCCURRENCES
) -> Optional[dict[str, Any]]:
    """Gaps-between-occurrences cadence check; a HEURISTIC, not a real scheduler.

    Parses ``timestamps`` (ISO-8601, any order), sorts them, and computes the
    gaps between consecutive occurrences in days. With fewer than
    ``min_occurrences`` timestamps there aren't enough gaps to say anything
    about regularity, so this returns ``None``. ``min_occurrences`` defaults to
    :data:`CADENCE_MIN_OCCURRENCES` but callers may relax it (down to a floor of
    2; see :func:`effective_threshold`) for a small window; a single
    occurrence is never a cadence, no matter how sparse the window. Otherwise
    it returns
    ``{"interval_days", "cv", "occurrences"}`` when the coefficient of variation
    (population stdev / mean of the gaps) is at or below :data:`CADENCE_MAX_CV`
   ; "low variance" meaning the gaps cluster tightly around one interval (e.g.
    ~daily, ~weekly). **Limits, stated honestly**: this is exact-count blind;
    it cannot distinguish "every Friday" from "every 7 days starting Tuesday",
    and a handful of points can look regular by chance. It is a proposal for a
    human to review, never an automatic schedule change. Never raises
    (unparseable/insufficient input degrades to ``None``).
    """
    try:
        parsed = []
        for ts in timestamps:
            if not ts:
                continue
            dt = datetime.fromisoformat(str(ts))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            parsed.append(dt)
        parsed.sort()
        min_occurrences = max(2, int(min_occurrences))  # never treat 1 as a cadence
        if len(parsed) < min_occurrences:
            return None
        gaps_days = [
            (b - a).total_seconds() / 86400.0 for a, b in zip(parsed, parsed[1:])
        ]
        if not gaps_days:
            return None
        mean = statistics.mean(gaps_days)
        if mean < CADENCE_MIN_INTERVAL_DAYS:
            # Sub-floor gaps (e.g. a burst of episodes seconds apart) are not a
            # "cadence" in any useful sense; they are just close together.
            return None
        stdev = statistics.pstdev(gaps_days) if len(gaps_days) > 1 else 0.0
        cv = stdev / mean
        if cv > CADENCE_MAX_CV:
            return None
        return {
            "interval_days": round(mean, 2),
            "cv": round(cv, 3),
            "occurrences": len(parsed),
        }
    except Exception:
        return None


async def detect_recurring(
    instance_id: str,
    *,
    days: int = DEFAULT_DAYS,
    min_count: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Find patterns that repeat OFTEN, regardless of cost; the broader sense.

    :func:`detect_repetition` only flags patterns that both repeated AND spent
    real money; a $0 pattern (a question asked many times, a trigger firing on
    a schedule-like cadence) never surfaces there even though it may still be
    worth a $0 intervention (a memory fact, a scheduled action, a cache seed).
    This is that broader sense: same pure-SQL aggregation over ``episodes``,
    but WITHOUT the cost filter, at a lower default bar
    (:data:`DEFAULT_RECURRING_THRESHOLD`).

    Each finding also tries a cadence check (see :func:`_cadence_stats`) once it
    has enough occurrences, and is tagged ``"judgment": True`` when the
    candidate action is one of the judgment shapes :func:`is_excluded` knows
    about (a chat reply, an org department's think); a judgment pattern can
    never become a ``mint_action`` tool, but it CAN be the subject of a memory
    fact / cache seed proposal (see :func:`recommend_intervention`), so it is
    NOT dropped here the way :func:`detect_repetition` drops it.

    On top of the episodes-based patterns, this also surfaces entries from the
    answer cache (:mod:`lhassa.memory.answer_cache`) that have been hit at
    least the effective ``min_count`` times; a REAL repeated question with
    REAL text (``question``/``answer`` keys on the finding), already served
    for $0. These are tagged ``"in_cache": True``.

    An explicit ``min_count`` ALWAYS wins unchanged. Otherwise the base
    (:data:`DEFAULT_RECURRING_THRESHOLD`) adapts to the window's population
    (total episodes in the window) via :func:`effective_threshold`: same
    reasoning as :func:`detect_repetition`. The cadence minimum
    (:data:`CADENCE_MIN_OCCURRENCES`) adapts the same way, floored at 2 (a
    single occurrence is never a cadence).

    Pure SQL + Python, $0, never raises (degrades to ``[]``).
    """
    explicit = min_count is not None
    base_min_count = min_count if explicit else DEFAULT_RECURRING_THRESHOLD
    if base_min_count < 1:
        base_min_count = DEFAULT_RECURRING_THRESHOLD
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows: list[dict[str, Any]] = []
    timestamps_by_pattern: dict[tuple[str, str], list[str]] = {}
    try:
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """SELECT COUNT(*) AS n FROM episodes
                   WHERE instance_id = ? AND creado_en >= ?""",
                (instance_id, cutoff),
            ) as pcur:
                prow = await pcur.fetchone()
            population = int((prow["n"] if prow else 0) or 0)
            used_min_count = (
                base_min_count
                if explicit
                else effective_threshold(base_min_count, population)
            )
            cadence_min = (
                CADENCE_MIN_OCCURRENCES
                if explicit
                else effective_threshold(
                    CADENCE_MIN_OCCURRENCES, population, floor=2
                )
            )
            async with conn.execute(
                """SELECT despertar_por, accion,
                          COUNT(*) AS n,
                          SUM(COALESCE(costo_usd, 0)) AS cost,
                          MAX(decision) AS decision_sample
                   FROM episodes
                   WHERE instance_id = ? AND creado_en >= ?
                   GROUP BY despertar_por, accion
                   HAVING COUNT(*) >= ?
                   ORDER BY n DESC""",
                (instance_id, cutoff, used_min_count),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

            for row in rows:
                if int(row.get("n") or 0) < cadence_min:
                    continue
                despertar_por = row.get("despertar_por") or ""
                accion = row.get("accion") or ""
                async with conn.execute(
                    """SELECT creado_en FROM episodes
                       WHERE instance_id = ? AND despertar_por = ? AND accion = ?
                             AND creado_en >= ?
                       ORDER BY creado_en""",
                    (instance_id, despertar_por, accion, cutoff),
                ) as cur2:
                    ts_rows = await cur2.fetchall()
                timestamps_by_pattern[(despertar_por, accion)] = [
                    r["creado_en"] for r in ts_rows
                ]
    except Exception:
        # A missing/corrupt table degrades to "nothing detected": this is a
        # cheap sense, not a critical path; it must never crash a caller.
        return []

    denied = await denied_patterns(instance_id)
    out: list[dict[str, Any]] = []
    for row in rows:
        despertar_por = row.get("despertar_por") or ""
        accion = row.get("accion") or ""
        if is_infrastructure(despertar_por, accion):
            continue  # Lhassa's own scheduled/org machinery; not owner work
        pattern = pattern_key(despertar_por, accion)
        if pattern in denied:
            continue
        cadence = _cadence_stats(
            timestamps_by_pattern.get((despertar_por, accion), []),
            min_occurrences=cadence_min,
        )
        out.append(
            {
                "pattern": pattern,
                "despertar_por": despertar_por,
                "accion": accion,
                "count": int(row.get("n") or 0),
                "total_cost_usd": float(row.get("cost") or 0.0),
                "judgment": is_excluded(accion, str(row.get("decision_sample") or "")),
                "cadence": cadence,
                "in_cache": False,
                "threshold_used": used_min_count,
            }
        )

    # A second, REAL source of repeated-question findings: the answer cache
    # already stores actual question/answer text and a hit counter; no
    # heuristic needed here, hits IS the repeat count.
    try:
        cache_rows = await _db.list_answer_cache(instance_id, limit=500)
    except Exception:
        cache_rows = []
    for row in cache_rows:
        # hits starts at 0 on the row that was actually stored; +1 counts the
        # original turn that seeded the cache as one occurrence too.
        count = int(row.get("hits") or 0) + 1
        if count < used_min_count:
            continue
        pattern = f"answer_cache::{row.get('id')}"
        if pattern in denied:
            continue
        out.append(
            {
                "pattern": pattern,
                "despertar_por": "telegram",
                "accion": "responder",
                "count": count,
                "total_cost_usd": 0.0,
                "judgment": True,
                "cadence": None,
                "in_cache": True,
                "question": row.get("question"),
                "answer": row.get("answer"),
                "threshold_used": used_min_count,
            }
        )

    out.sort(key=lambda f: -f["count"])
    return out


async def detect_routable(
    instance_id: str,
    *,
    days: int = DEFAULT_DAYS,
    min_count: Optional[int] = None,
) -> list[dict[str, Any]]:
    """The self-routing consciousness: capabilities that keep paying for a
    think/research that a $0 deterministic handler could already serve.

    :mod:`lhassa.chat.think` (the SHELL) tags an episode's
    ``decision`` with ``"[routable:<capability>]"`` (see
    :func:`lhassa.notify.capabilities.tag_intent`) whenever a message a
    capability recognises still fell through to a real think or the research
    bridge. This is the pure-SQL read-back half of that seam: group the
    window's tagged episodes by capability, count them, and sum their cost.

    Only capabilities seen at least the effective ``min_count`` times are
    returned (a single miss isn't a pattern); cost is NOT required to be
    positive here (unlike :func:`detect_repetition`): even a $0
    deferred/free-provider think that still went through the judgment path is
    worth routing away, since the point is to stop spending JUDGMENT (tokens,
    latency, risk of drift), not only dollars. An explicit ``min_count``
    ALWAYS wins unchanged; otherwise the base (:data:`DEFAULT_ROUTABLE_THRESHOLD`)
    adapts to the window's population (the tagged episodes already scanned,
    before grouping by capability) via :func:`effective_threshold`. Each item:
    ``{"pattern", "capability", "count", "total_cost_usd", "threshold_used"}``.
    Pure SQL + regex, $0, never raises (degrades to ``[]``); this NEVER
    imports :mod:`lhassa.notify` (agents/ stays shell-free); it only reads the
    plain text another layer already wrote.
    """
    explicit = min_count is not None
    base_min_count = min_count if explicit else DEFAULT_ROUTABLE_THRESHOLD
    if base_min_count < 1:
        base_min_count = DEFAULT_ROUTABLE_THRESHOLD
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """SELECT decision, COALESCE(costo_usd, 0) AS cost
                   FROM episodes
                   WHERE instance_id = ? AND creado_en >= ?
                     AND decision LIKE '%[routable:%'""",
                (instance_id, cutoff),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
    except Exception:
        # A missing/corrupt table degrades to "nothing detected": this is a
        # cheap sense, not a critical path; it must never crash a caller.
        return []

    # Population = the tagged episodes already scanned above, before grouping
    # by capability; the same "candidates it already scans" rule the other
    # two detectors use.
    used_min_count = (
        base_min_count if explicit else effective_threshold(base_min_count, len(rows))
    )

    stats: dict[str, dict[str, Any]] = {}
    for row in rows:
        m = _ROUTABLE_RE.search(str(row.get("decision") or ""))
        if not m:
            continue
        cap = m.group(1).lower()
        entry = stats.setdefault(cap, {"count": 0, "total_cost_usd": 0.0})
        entry["count"] += 1
        entry["total_cost_usd"] += float(row.get("cost") or 0.0)

    denied = await denied_patterns(instance_id)
    out: list[dict[str, Any]] = []
    for cap, entry in stats.items():
        if entry["count"] < used_min_count:
            continue
        pattern = f"routable::{cap}"
        if pattern in denied:
            continue  # the owner already said no to routing this capability
        out.append(
            {
                "pattern": pattern,
                "capability": cap,
                "count": entry["count"],
                "total_cost_usd": entry["total_cost_usd"],
                "threshold_used": used_min_count,
            }
        )
    out.sort(key=lambda f: -f["count"])
    return out


def recommend_intervention(finding: dict[str, Any]) -> dict[str, Any]:
    """The "consciousness" reasoning: pick the CHEAPEST fix for one finding.

    Deterministic, $0; no model call. ``finding`` is one item from
    :func:`detect_repetition`, :func:`detect_recurring`, or
    :func:`detect_mechanical_chains`; this looks only at its SHAPE (never at
    outside state) to decide which intervention is cheapest, in priority order:

      0. a ``capability`` key (a :func:`detect_routable` finding) →
         ``kind="route"``: a deterministic $0 handler for this class of
         message ALREADY EXISTS (see :mod:`lhassa.notify.capabilities`); the fix
         is not to mint anything new, just to switch its route on.
      1. a ``chain`` key → ``kind="script"``: the existing PTC path
         (:func:`draft_script_for_chain`) collapses a mechanical action chain.
      2. a ``cadence`` key (regular gaps, see :func:`_cadence_stats`) →
         ``kind="schedule"``: propose a recurring $0 action instead of
         re-deriving the same judgment on a rhythm.
      3. ``judgment=True`` (a chat reply / org think; never automatable as a
         tool, see :func:`is_excluded`) →
           - ``in_cache=True`` → ``kind="cache_seed"``: it is ALREADY being
             served for $0 from the answer cache; nothing further to mint,
             the rationale just names the savings.
           - otherwise → ``kind="memory_fact"``: the brain keeps re-deriving
             the same answer; propose storing it as a durable fact instead.
      4. anything else (a genuinely costly, non-judgment, non-chain pattern
         with no cheaper fix available) → ``kind="tool"``: the original
         ``mint_action`` path.

    Returns ``{"kind", "rationale", "payload"}``. ``rationale`` names the
    tokens/cost/repeats it would save, in plain text. Never raises; a
    malformed ``finding`` degrades to a ``"tool"`` recommendation with a
    best-effort rationale.
    """
    try:
        pattern = finding.get("pattern")
        count = int(finding.get("count") or 0)
        cost = float(finding.get("total_cost_usd") or 0.0)
        despertar_por = finding.get("despertar_por") or "?"
        accion = finding.get("accion") or "?"

        capability = finding.get("capability")
        if capability:
            return {
                "kind": "route",
                "rationale": (
                    f"'{capability}' questions went to a think/research ×{count} "
                    f"(${cost:.4f} spent) even though a $0 deterministic handler "
                    "already exists; approving routes them there for good"
                ),
                "payload": {
                    "pattern": pattern,
                    "capability": capability,
                    "count": count,
                    "total_cost_usd": cost,
                },
            }

        chain = finding.get("chain")
        if chain:
            chain = list(chain)
            label = " → ".join(chain)
            return {
                "kind": "script",
                "rationale": (
                    f"mechanical chain {label} repeated ×{count}; collapsing it "
                    "into one reviewed script (PTC) replaces N fixed-action "
                    "episodes with one $0 run"
                ),
                "payload": {
                    "pattern": pattern,
                    "despertar_por": despertar_por,
                    "chain": chain,
                    "count": count,
                    "draft_script": draft_script_for_chain(chain),
                },
            }

        cadence = finding.get("cadence")
        if cadence:
            interval = cadence.get("interval_days")
            return {
                "kind": "schedule",
                "rationale": (
                    f"'{despertar_por}' fires on a ~{interval}-day cadence "
                    f"(×{count}, cv={cadence.get('cv')}); a scheduled $0 action "
                    "beats re-deriving the same judgment on a rhythm"
                ),
                "payload": {
                    "pattern": pattern,
                    "despertar_por": despertar_por,
                    "accion": accion,
                    "interval_days": interval,
                    "count": count,
                    "recurrence_seconds": int(round((interval or 0) * 86400)),
                },
            }

        if finding.get("judgment"):
            if finding.get("in_cache"):
                return {
                    "kind": "cache_seed",
                    "rationale": (
                        f"already answered from the cache ×{count}; this is "
                        "already $0, nothing further to mint"
                    ),
                    "payload": {
                        "pattern": pattern,
                        "question": finding.get("question"),
                        "answer": finding.get("answer"),
                        "count": count,
                    },
                }
            return {
                "kind": "memory_fact",
                "rationale": (
                    f"'{despertar_por}' keeps re-deriving the same answer ×{count} "
                    f"(${cost:.4f} spent so far); a durable fact removes the "
                    "re-derivation entirely"
                ),
                "payload": {
                    "pattern": pattern,
                    "despertar_por": despertar_por,
                    "accion": accion,
                    "count": count,
                    "total_cost_usd": cost,
                },
            }

        return {
            "kind": "tool",
            "rationale": (
                f"'{accion}' repeated ×{count} costing ${cost:.4f} with no cheaper "
                "fix available; mint it as a deterministic $0 action"
            ),
            "payload": {
                "pattern": pattern,
                "despertar_por": despertar_por,
                "candidate_accion": accion,
                "count": count,
                "total_cost_usd": cost,
            },
        }
    except Exception:
        return {
            "kind": "tool",
            "rationale": "degraded: could not reason about this finding",
            "payload": {"pattern": (finding or {}).get("pattern")},
        }


def _ident(accion: str) -> str:
    """A valid Python identifier for an action name (mirrors the PTC stub rule)."""
    cleaned = re.sub(r"\W", "_", (accion or "").strip()) or "accion"
    return cleaned if not cleaned[0].isdigit() else f"_{cleaned}"


def draft_script_for_chain(chain: list[str]) -> str:
    """A DETERMINISTIC draft PTC script for a repeated action chain; no model call.

    The draft is what the owner reviews in the ``mint_script`` approval: the
    chain's actions called in order through the ``lhassa_actions`` stub, params
    left as explicit TODOs. Approving records the intent; actually RUNNING the
    script still crosses the approval frontier (``run_script`` has
    ``requires_approval=True``).
    """
    lines = [
        '"""Draft: collapse a repeated action chain into one PTC script.',
        "",
        f"Chain: {' -> '.join(chain)}",
        'Review and fill the TODO params before approving a run."""',
        "import lhassa_actions as la",
        "",
    ]
    for i, accion in enumerate(chain, start=1):
        lines.append(f"r{i} = la.{_ident(accion)}()  # TODO: params for {accion!r}")
    lines.append("")
    lines.append(f'print("chain of {len(chain)} action(s) done")')
    return "\n".join(lines)


async def detect_mechanical_chains(
    instance_id: str,
    *,
    days: int = DEFAULT_DAYS,
    threshold: int = DEFAULT_CHAIN_THRESHOLD,
    max_len: int = DEFAULT_CHAIN_MAX_LEN,
) -> list[dict[str, Any]]:
    """Repeated MECHANICAL action chains; the PTC (``run_script``) candidates.

    Reads the window's NON-think episodes (a judgment never enters a chain) in
    time order per ``despertar_por``, slides windows of length 2..``max_len``
    over each trigger's action sequence, and reports every exact chain (≥2
    DISTINCT actions) recurring ≥ ``threshold`` times. v1 limitation, stated
    honestly: windows may overlap, so a dense run can over-count; the finding
    is a proposal for a HUMAN to review, never an automatic act. Pure SQL +
    Python, $0, never raises (degrades to ``[]``).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """SELECT despertar_por, accion FROM episodes
                   WHERE instance_id = ? AND creado_en >= ?
                     AND COALESCE(accion, '') != ''
                     AND LOWER(COALESCE(decision, '')) NOT LIKE 'think%'
                   ORDER BY despertar_por, creado_en""",
                (instance_id, cutoff),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
    except Exception:
        return []

    sequences: dict[str, list[str]] = {}
    for row in rows:
        accion = str(row.get("accion") or "")
        despertar_por = str(row.get("despertar_por") or "")
        if is_excluded(accion) or is_infrastructure(despertar_por, accion):
            continue  # a judgment, or Lhassa's own $0 machinery; never a chain
        sequences.setdefault(despertar_por, []).append(accion)

    counts: dict[tuple[str, tuple[str, ...]], int] = {}
    for despertar_por, seq in sequences.items():
        for size in range(2, max_len + 1):
            for start in range(0, len(seq) - size + 1):
                window = tuple(seq[start : start + size])
                if len(set(window)) < 2:
                    continue  # a chain needs ≥2 DISTINCT actions
                key = (despertar_por, window)
                counts[key] = counts.get(key, 0) + 1

    out: list[dict[str, Any]] = []
    for (despertar_por, window), n in counts.items():
        if n < threshold:
            continue
        chain = list(window)
        out.append(
            {
                "pattern": chain_pattern_key(despertar_por, chain),
                "despertar_por": despertar_por,
                "chain": chain,
                "count": n,
            }
        )
    out.sort(key=lambda f: -f["count"])
    return out
