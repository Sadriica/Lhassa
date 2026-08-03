# SPDX-License-Identifier: AGPL-3.0-or-later
"""The autonomy knob — friction that lowers with repeated human approval (§2.6, #9).

"La autonomía se gana con evidencia. Supervisado primero; perilla que baja con
aprobaciones repetidas." This module tracks, per ``(accion, actor_id)`` within an
instance, the count of CONSECUTIVE human-origin approvals of that action. Once
that streak reaches ``Config.autonomy_threshold`` (default 3) the action
auto-approves: "una `humano` repetida se vuelve automática".

Three hard rules, straight from the brief and the invariants:

1. **Per-action, per-actor — never global.** HITL is by flow. The streak is keyed
   on the specific action AND the specific WHO, so trust earned for drafting a
   document never leaks into moving money.

2. **The high-risk set is never auto-promoted.** Actions in
   ``Config.always_approve_actions`` (e.g. move_money / delete_data) ALWAYS require
   human approval regardless of history — "mover-dinero exige aprobación aunque un
   borrador no". ``should_auto_approve`` returns ``False`` for them unconditionally.

3. **The knob never raises trust.** It only LOWERS friction for actions a human has
   already repeatedly approved; it does NOT promote an ``Origin``'s ``TrustLevel``
   (principle #7: "la confianza se degrada por contagio, nunca se promueve"). A
   denial resets the streak to zero.

The counters live in the ``autonomy`` table of the "grande" (``core.db``), keyed by
``instance_id`` like every other row. These helpers are ``async`` because the store
is async; the brief's ``def`` signatures are illustrative.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import db
from lhassa.security.trust import TrustLevel

# ── feedback → knob bridge (§2.2 — the knob should move from lived feedback,
# not only from explicit approve/deny taps) ─────────────────────────────────

# The file-perception actions are gated per-access by protected roots
# (``security/allowlist.py``) — their approval is intentionally per-call, never
# a streak. Feedback must never touch them (hard rule #3 below).
_PROTECTED_ROOT_ACTIONS = frozenset({"read_file", "list_dir"})

# How many negative signals attributed to the same accion, within one window,
# force a reset — being wrong repeatedly must cost autonomy.
_NEGATIVE_RESET_THRESHOLD = 2

# How far (minutes) before a scored "responder" episode we look for the nearest
# concrete (non-"responder") episode to attribute a signal to — mirrors the
# windows already used by ``core.feedback`` for its own heuristics.
_ATTRIBUTION_WINDOW_MINUTES = 30


def _always_approve() -> "set[str]":
    """The configured high-risk action set (never auto-promoted)."""
    actions = Config.always_approve_actions
    return actions if isinstance(actions, set) else set(actions or [])


def _threshold() -> int:
    """Consecutive-human-approval count after which an action auto-approves."""
    return int(Config.autonomy_threshold)


def _key_actor(actor_id: Optional[str]) -> str:
    """Normalize an actor id for the streak key (``None`` → ``""``, unattributed)."""
    return actor_id or ""


async def autonomy_level(instance_id: str, accion: str, actor_id: Optional[str]) -> int:
    """Return the current consecutive-human-approval streak for (accion, actor_id)."""
    return await db.get_autonomy_streak(instance_id, accion, _key_actor(actor_id))


async def should_auto_approve(
    instance_id: str, accion: str, actor_id: Optional[str]
) -> bool:
    """True if this (accion, actor_id) has earned auto-approval.

    Returns ``False`` for any action in the high-risk always-approve set,
    regardless of how long its streak is (hard rule #2). Otherwise true once the
    streak has reached the configured threshold.
    """
    if accion in _always_approve():
        return False
    return (await autonomy_level(instance_id, accion, actor_id)) >= _threshold()


async def record_decision(
    instance_id: str,
    accion: str,
    actor_id: Optional[str],
    *,
    approved: bool,
    origen_trust: Optional[int] = None,
) -> int:
    """Feed one approval decision into the knob and return the resulting streak.

    - A **denial** resets the streak to zero (the autonomy must be re-earned).
    - An **approval of a HUMANO-origin action** advances the streak by one — this
      is the only thing that builds autonomy ("una `humano` repetida se vuelve
      automática").
    - An approval whose action did NOT originate from a human leaves the streak
      unchanged: it neither builds autonomy (no contagion-promotion) nor punishes
      an otherwise-clean history.

    ``origen_trust`` is the ``TrustLevel`` (int) of the action that was parked for
    approval; when it is unknown we treat the approval as human-origin (the common
    case is a human directly approving their own parked action).
    """
    actor = _key_actor(actor_id)
    if not approved:
        await db.reset_autonomy_streak(instance_id, accion, actor)
        return 0
    if origen_trust is not None and int(origen_trust) < int(TrustLevel.HUMANO):
        # Non-human-origin approval: do not build autonomy (never promote trust).
        return await db.get_autonomy_streak(instance_id, accion, actor)
    return await db.bump_autonomy_streak(instance_id, accion, actor)


def _excluded_actions() -> "set[str]":
    """Actions the feedback bridge must never touch (hard rule #3)."""
    return _always_approve() | set(_PROTECTED_ROOT_ACTIONS)


def _feedback_marker_key(window_days: int, bucket: str) -> str:
    """One idempotency marker per (window length, day) — see ``apply_feedback``."""
    return f"autonomy_feedback:{int(window_days)}d:{bucket}"


async def _episode_row(conn: Any, instance_id: str, episode_id: str) -> Optional[dict]:
    """Raw lookup of one episode's ``accion``/``creado_en`` by id. Never raises."""
    try:
        async with conn.execute(
            "SELECT accion, creado_en FROM episodes WHERE instance_id = ? AND id = ?",
            (instance_id, episode_id),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row is not None else None
    except Exception as exc:
        logger.debug(f"autonomy.apply_feedback: episode lookup failed: {exc}")
        return None


async def _subject_accion(conn: Any, instance_id: str, creado_en: str) -> Optional[str]:
    """The nearest concrete (non-"responder") accion around ``creado_en``.

    A "responder" episode is a chat answer, never itself a controllable action;
    this looks EITHER SIDE (a preceding action the answer reported on, or a
    following action the answer prompted — e.g. the "acted-on" heuristic's
    trigger) within :data:`_ATTRIBUTION_WINDOW_MINUTES`, and picks whichever is
    closest in time. ``None`` when nothing concrete is nearby — the caller
    must SKIP rather than guess.
    """
    try:
        dt = datetime.fromisoformat(creado_en)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    lo = (dt - timedelta(minutes=_ATTRIBUTION_WINDOW_MINUTES)).isoformat()
    hi = (dt + timedelta(minutes=_ATTRIBUTION_WINDOW_MINUTES)).isoformat()
    try:
        async with conn.execute(
            """SELECT accion, creado_en FROM episodes
               WHERE instance_id = ? AND creado_en >= ? AND creado_en <= ?
                 AND accion IS NOT NULL AND accion != '' AND accion != 'responder'
               ORDER BY creado_en""",
            (instance_id, lo, hi),
        ) as cur:
            rows = await cur.fetchall()
    except Exception as exc:
        logger.debug(f"autonomy.apply_feedback: subject lookup failed: {exc}")
        return None

    best_accion: Optional[str] = None
    best_diff: Optional[float] = None
    for row in rows:
        try:
            rdt = datetime.fromisoformat(row["creado_en"])
        except (ValueError, TypeError):
            continue
        if rdt.tzinfo is None:
            rdt = rdt.replace(tzinfo=timezone.utc)
        diff = abs((rdt - dt).total_seconds())
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_accion = row["accion"]
    return best_accion


async def _actors_for_accion(
    conn: Any, instance_id: str, accion: str, *, min_streak: int = 0
) -> "list[str]":
    """Actors that already hold an autonomy row for ``accion``, streak >= min_streak.

    Feedback has no actor identity of its own (the episode log carries no
    ``actor_id``) — it can only REINFORCE or RESET trust a human already
    started, per (accion, actor). Never raises (empty list on any error).
    """
    try:
        async with conn.execute(
            """SELECT actor_id FROM autonomy
               WHERE instance_id = ? AND accion = ? AND streak >= ?""",
            (instance_id, accion, int(min_streak)),
        ) as cur:
            rows = await cur.fetchall()
        return [row["actor_id"] for row in rows]
    except Exception as exc:
        logger.debug(f"autonomy.apply_feedback: actor lookup failed: {exc}")
        return []


async def apply_feedback(
    instance_id: str, *, window_days: int = 7, config: Any = None
) -> dict:
    """Move the autonomy knob from LIVED feedback, not only explicit taps (§2.2).

    Reads :func:`lhassa.core.feedback.derive_signals` over the last
    ``window_days`` and translates the behavioral signals into per-accion knob
    adjustments. Safety invariants (all enforced here, not just documented):

    1. **Only negative evidence acts strongly.** Two or more negative signals
       (re-ask/correction) attributed to the SAME accion within the window
       RESET that accion's streak for every actor currently holding one — the
       same effect as a human denial. Being wrong repeatedly must cost
       autonomy.
    2. **Positive/silence evidence acts weakly.** It adds AT MOST +1 streak per
       accion per window, and ONLY for actors who already hold a streak >= 1
       for that accion — a human must have started the trust; feedback can
       only reinforce it, never bootstrap it from nothing.
    3. **Never touch high-risk or per-access actions.** Anything in
       ``Config.always_approve_actions`` (never auto-promoted regardless of
       history) and the file-perception actions gated by protected roots
       (``read_file``, ``list_dir`` — approval is per-access by design) are
       always skipped, even with strong feedback in either direction.
    4. **Attribution never guesses.** A signal is only actioned when it maps to
       a concrete accion: either the scored episode's own ``accion`` (when it
       is a real action, not the generic "responder" chat answer) or the
       nearest concrete action episode shortly before it. Anything else —
       including every "neutral"/inconclusive signal — is counted in
       ``skipped``, never applied.
    5. **Idempotent per window.** Guarded by a ``watch_state`` marker keyed on
       ``(window_days, day)`` — like ``org/inbox_sense.py``'s dedup pattern —
       so re-running within the same day is a $0 no-op.

    Returns ``{"reset": [...], "reinforced": [...], "skipped": n,
    "window_days": d}`` (accion names, deduped). Never raises: any failure
    degrades to an all-zero report.
    """
    report: dict = {"reset": [], "reinforced": [], "skipped": 0, "window_days": int(window_days)}
    try:
        cfg = config or Config
        from lhassa._vendor import aiosqlite
        from lhassa.core import feedback as core_feedback

        bucket = datetime.now(timezone.utc).date().isoformat()
        marker_key = _feedback_marker_key(window_days, bucket)
        already = await db.get_watch_state(instance_id, marker_key)
        if already:
            return report

        signals = await core_feedback.derive_signals(instance_id, days=window_days, config=cfg)
        if not signals:
            await db.set_watch_state(instance_id, marker_key, "done")
            return report

        excluded = _excluded_actions()
        negative_counts: dict[str, int] = {}
        positive_hit: "set[str]" = set()

        await db.init_db()
        async with aiosqlite.connect(db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            for sig in signals:
                ep = await _episode_row(conn, instance_id, sig.get("episode_id", ""))
                if not ep or not ep.get("creado_en"):
                    report["skipped"] += 1
                    continue

                accion = ep.get("accion") or ""
                if not accion or accion == core_feedback.ANSWER_ACCION:
                    accion = await _subject_accion(conn, instance_id, ep["creado_en"])
                if not accion:
                    report["skipped"] += 1
                    continue
                if accion in excluded:
                    report["skipped"] += 1
                    continue

                signal = sig.get("signal")
                if signal == "negative":
                    negative_counts[accion] = negative_counts.get(accion, 0) + 1
                elif signal == "positive":
                    positive_hit.add(accion)
                else:
                    report["skipped"] += 1

            for accion, count in negative_counts.items():
                if count < _NEGATIVE_RESET_THRESHOLD:
                    report["skipped"] += 1
                    continue
                actors = await _actors_for_accion(conn, instance_id, accion)
                if not actors:
                    report["skipped"] += 1
                    continue
                for actor in actors:
                    await db.reset_autonomy_streak(instance_id, accion, actor)
                report["reset"].append(accion)

            for accion in positive_hit:
                if accion in report["reset"]:
                    # Negative evidence for this accion already fired this
                    # window — never let a weak positive undo a strong reset.
                    continue
                actors = await _actors_for_accion(conn, instance_id, accion, min_streak=1)
                if not actors:
                    report["skipped"] += 1
                    continue
                for actor in actors:
                    await db.bump_autonomy_streak(instance_id, accion, actor)
                report["reinforced"].append(accion)

        await db.set_watch_state(instance_id, marker_key, "done")
        return report
    except Exception as exc:
        logger.debug(f"autonomy.apply_feedback failed: {exc}")
        return {"reset": [], "reinforced": [], "skipped": 0, "window_days": int(window_days)}
