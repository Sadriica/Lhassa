"""
Repeated-think detector — the $0 sense that spots automatable patterns (§2.8).

The self-improvement pipeline (quarantine -> gates -> versioning) has always had
a missing FIRST step: nothing ever noticed that the brain was judging (spending
tokens) on essentially the same thing over and over. This module is that sense.

Detection is a PURE SQL aggregation over the episodes table (the append-only
grande): group recent episodes by (despertar_por, accion), count occurrences and
sum cost. A pattern that repeats often AND cost real money is a strong signal a
deterministic action could replace the judgment entirely. This never calls an
LLM and never writes anything — see :mod:`lhassa.actionpack._selfimprove` for the
action that turns a finding into a parked approval + notification.

**A judgment is never a valid candidate.** The detector's first five real
proposals were "make 'responder' automatic" (the Telegram chat turn itself) and
four "make 'dept:<x> think' automatic" (an org department's judgment) — the
owner denied all five, because neither can EVER be a deterministic action: a
chat reply and a department's "think" are, by definition, the judgment the
self-improvement loop exists to relieve, not something downstream of it.
:func:`is_excluded` is the explicit predicate that keeps these out of every
finding this module returns.

This module also finds a second, complementary signal: repeated MECHANICAL
ACTION CHAINS (:func:`detect_mechanical_chains`) — an ordered sequence of ≥2
distinct deterministic actions recurring under the same trigger. That is not a
"mint one action" candidate; it is a Programmatic Tool Calling (PTC, see
:mod:`lhassa.agents.ptc`) candidate — collapse the chain into one script a
human reviews once instead of N separate fixed-action episodes.
"""

from __future__ import annotations

import json
import re
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

# Candidate action names that are ALWAYS a judgment, never automatable — see the
# module docstring for the real denied proposals this encodes.
_EXCLUDED_ACCIONES = {"responder"}
# An org department's judgment think, e.g. "dept:interns think".
_DEPT_THINK_RE = re.compile(r"^dept:.+ think$")


def pattern_key(despertar_por: str, accion: str) -> str:
    """Normalized grouping key for one repeated-think pattern."""
    return f"{(despertar_por or '').strip()}::{(accion or '').strip()}"


def chain_pattern_key(despertar_por: str, chain: list[str]) -> str:
    """Normalized grouping key for one repeated mechanical action chain."""
    return f"{(despertar_por or '').strip()}::chain:{'→'.join(chain)}"


def is_excluded(accion: str, decision: str = "") -> bool:
    """True when a pattern can NEVER become a deterministic action — never propose it.

    A judgment (a chat reply, an org department's "think") cannot be minted as a
    $0 fixed action by definition: doing so would just hide an LLM call behind a
    fake deterministic name, defeating the point of the self-improvement loop.
    Excludes any pattern whose:

      - candidate ``accion`` is exactly ``"responder"`` — the tool-less
        Telegram/chat think (see ``notify.telegram_inbound``'s fallback reply).
      - candidate ``accion`` matches ``"dept:<name> think"`` — an org
        department's judgment (see ``org.coordinator``/``org.executor``).
      - recorded ``decision`` text starts with ``"think"`` — a second, more
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
    """Patterns whose most recent ``mint_action``/``mint_script`` proposal was DENIED.

    ``watch_state`` (see ``actionpack._selfimprove._SEEN_PREFIX``) only stops a
    pattern from being re-proposed while its approval is still PENDING; once a
    human resolves it ``rechazada`` it falls out of that dedup path entirely, and
    the pattern would otherwise be proposed again on the very next run. A denial
    is a human decision, not a queue timeout — this reads the approvals table
    directly (``core.db`` exposes no "list denied" helper) so the owner is never
    asked twice for an automation they already said no to. Pure SQL, $0, never
    raises (degrades to an empty set).
    """
    patterns: set[str] = set()
    try:
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """SELECT payload FROM approvals
                   WHERE instance_id = ? AND estado = 'rechazada'
                     AND tipo IN ('mint_action', 'mint_script')""",
                (instance_id,),
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
    """Find (despertar_por, accion) patterns repeated >= ``threshold`` times.

    Pure SQL aggregation over the last ``days`` of episodes for ``instance_id`` —
    no LLM, no cost, never raises destructively. Only patterns that both repeat
    at least ``threshold`` times AND have spent real money (``total_cost_usd >
    0``) are returned — a pattern that already costs nothing is not worth
    automating further. Sorted by repeat count descending.

    Each item: ``{"pattern", "despertar_por", "accion", "count", "total_cost_usd"}``.
    """
    threshold = (
        threshold if threshold is not None else int(Config.repetition_threshold)
    )
    if threshold < 1:
        threshold = DEFAULT_THRESHOLD
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
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
                (instance_id, cutoff, threshold),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
    except Exception:
        # A missing/corrupt table degrades to "nothing detected" — this is a
        # cheap sense, not a critical path; it must never crash a caller.
        return []

    denied = await denied_patterns(instance_id)
    out: list[dict[str, Any]] = []
    for row in rows:
        despertar_por = row.get("despertar_por") or ""
        accion = row.get("accion") or ""
        if is_excluded(accion, str(row.get("decision_sample") or "")):
            continue  # a judgment can never become a deterministic action
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
            }
        )
    return out


def _ident(accion: str) -> str:
    """A valid Python identifier for an action name (mirrors the PTC stub rule)."""
    cleaned = re.sub(r"\W", "_", (accion or "").strip()) or "accion"
    return cleaned if not cleaned[0].isdigit() else f"_{cleaned}"


def draft_script_for_chain(chain: list[str]) -> str:
    """A DETERMINISTIC draft PTC script for a repeated action chain — no model call.

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
    """Repeated MECHANICAL action chains — the PTC (``run_script``) candidates.

    Reads the window's NON-think episodes (a judgment never enters a chain) in
    time order per ``despertar_por``, slides windows of length 2..``max_len``
    over each trigger's action sequence, and reports every exact chain (≥2
    DISTINCT actions) recurring ≥ ``threshold`` times. v1 limitation, stated
    honestly: windows may overlap, so a dense run can over-count — the finding
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
        if is_excluded(accion):
            continue
        sequences.setdefault(str(row.get("despertar_por") or ""), []).append(accion)

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
