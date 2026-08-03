# SPDX-License-Identifier: AGPL-3.0-or-later
"""The $0 feedback signal — did an answer land, WITHOUT ever asking the owner.

Lhassa never shows a thumbs-up/down. This module infers, from behavior already
sitting in the episode log, whether a past answer probably landed or not — pure
SQL + Python over ``episodes``/``approvals``, no LLM call, never raises. It is a
**consumer** of the grande, same posture as :mod:`lhassa.agents.repetition`
(which reads episodes to find repeated *thinks*; this reads them to find
repeated *answers*).

Honest limitation up front: the episode schema (see :mod:`lhassa.core.episode`)
has **no column for the owner's raw input text** — only a synthesized
``decision`` (e.g. ``"think: provider=X model=Y"`` or ``"cache-hit: ..."``) and
the assistant's own ``resultado`` (its reply). Every heuristic below works
within that constraint and is documented as an approximation, not ground
truth — this module infers a *probability the answer landed*, it does not know.

Four heuristics, each producing a per-episode ``{episode_id, signal, reason,
confidence}``:

1. **Re-ask** (negative) — a later "responder" episode serves a near-identical
   (or literally cache-served, ``decision`` starting with ``"cache-hit:"``)
   answer shortly after. Since the answer text recurring implies the
   *question* recurred (the owner asked essentially the same thing again),
   the ORIGINAL answer probably didn't land. Example: episode A replies
   "It's 18°C in Santiago." at 10:00; episode B replies the same text (a
   cache hit, ``decision="cache-hit: served past answer (sim=0.94)"``) at
   10:20 — A gets a negative "re-ask" signal.
   *Risk*: two genuinely different questions that happen to get a similarly
   short/canned answer ("Yes.", "Done.") can false-positive as a re-ask.

2. **Correction** (negative) — the very next answer episode's own recorded
   text (``decision`` + ``resultado`` combined — see the limitation above)
   opens with a correction phrase ("no", "wrong", "en realidad", "más
   bien", …). Example: episode A answers a question; episode B's text
   begins "No, en realidad quería..." — A gets a negative "correction"
   signal. *Honest caveat*: none of the currently-wired channels
   (telegram/voice) log the owner's raw follow-up into an episode field
   today (see the module limitation), so on live data this heuristic is
   presently dormant — it activates the moment any channel starts
   recording the follow-up text there. The logic itself is exercised and
   correct against seeded episodes.

3. **Silence-after** (weak positive) — no re-ask, no correction, and enough
   time (``silence_window_minutes``) has passed since the answer with
   nothing following (or the next answer on record is well past the
   window) → probably landed. Example: episode A answers at 09:00; the
   next chat episode is at 14:00 the same day (or there is none yet and
   hours have passed) — A gets a weak-positive "silence-after" signal.
   *Risk*: silence can also mean the owner gave up, not that they were
   satisfied — this is why the signal is WEAK, not strong.

4. **Acted-on** (positive) — a concrete action followed shortly after: a
   later episode in the window with a real (non-"responder") ``accion``,
   or an approval created and resolved ``"aprobada"`` in the window.
   Example: episode A answers "/code ..." parks a ``code_run`` approval;
   minutes later that approval is approved — A gets a positive "acted-on"
   signal (they used it). *Risk*: an unrelated action happening to fall in
   the same window (busy period) can be misattributed.

:func:`summary` aggregates counts + a ``net_score`` — a cheap "am I landing?"
health read for the owner/dashboard, still $0.
"""

from __future__ import annotations

import difflib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger

from lhassa._vendor import aiosqlite
from lhassa.core import db as _db

# ── tunables (kept local — no Config edit needed for this first cut) ───────────

DEFAULT_DAYS = 7
ANSWER_ACCION = "responder"

# How soon a duplicate/near-duplicate answer counts as "the same question came
# back" (re-ask). Kept generous: a re-ask an hour or two later is still a
# re-ask, days later is a fresh question.
REASK_WINDOW_MINUTES = 120
REASK_SIMILARITY = 0.85  # difflib ratio for "near-identical" answer text

CORRECTION_WINDOW_MINUTES = 30
ACTED_ON_WINDOW_MINUTES = 30
SILENCE_WINDOW_MINUTES = 60

# Correction-trigger prefixes (English + Spanish), matched at the START of the
# (lowercased, stripped) follow-up text — see the module docstring's caveat on
# where this text currently comes from.
_CORRECTION_PREFIXES = (
    "no,",
    "no ",
    "not that",
    "not what",
    "wrong",
    "that's not",
    "that is not",
    "en realidad",
    "mas bien",
    "más bien",
    "eso no es",
    "no es eso",
)


def _norm_text(text: Optional[str]) -> str:
    """Lowercase, whitespace-collapsed text for comparison. Never raises."""
    return " ".join(str(text or "").split()).lower()


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse an episode's ISO timestamp; ``None`` on anything unparsable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_correction(text: Optional[str]) -> bool:
    """True if ``text`` opens with a correction-signaling phrase."""
    norm = _norm_text(text)
    return any(norm.startswith(p) for p in _CORRECTION_PREFIXES)


def answers_match(a: Optional[str], b: Optional[str]) -> tuple[bool, float]:
    """Return ``(is_near_duplicate, ratio)`` for two answer texts.

    Exact match (after normalizing whitespace/case) short-circuits to a ratio
    of 1.0; otherwise a cheap ``difflib`` similarity ratio, $0 and pure Python.
    """
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return False, 0.0
    if na == nb:
        return True, 1.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    return ratio >= REASK_SIMILARITY, ratio


async def _fetch_episodes(instance_id: str, cutoff: str) -> list[dict]:
    """Episodes for ``instance_id`` since ``cutoff``, oldest first. Never raises."""
    try:
        await _db.init_db()
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """SELECT id, despertar_por, decision, accion, resultado, creado_en
                   FROM episodes
                   WHERE instance_id = ? AND creado_en >= ?
                   ORDER BY creado_en, rowid""",
                (instance_id, cutoff),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]
    except Exception as exc:
        logger.debug(f"feedback: could not read episodes: {exc}")
        return []


async def _fetch_approvals(instance_id: str, cutoff: str) -> list[dict]:
    """Approvals for ``instance_id`` since ``cutoff``, oldest first. Never raises."""
    try:
        await _db.init_db()
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """SELECT id, estado, creado_en, resuelto_en
                   FROM approvals
                   WHERE instance_id = ? AND creado_en >= ?
                   ORDER BY creado_en, rowid""",
                (instance_id, cutoff),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]
    except Exception as exc:
        logger.debug(f"feedback: could not read approvals: {exc}")
        return []


def _signal(episode_id: str, signal: str, reason: str, confidence: float) -> dict:
    return {
        "episode_id": episode_id,
        "signal": signal,
        "reason": reason,
        "confidence": round(confidence, 2),
    }


async def derive_signals(
    instance_id: str,
    *,
    days: int = DEFAULT_DAYS,
    config: Any = None,
) -> list[dict]:
    """Infer a feedback signal for each recent "answer" episode. Never raises.

    Walks ``accion == "responder"`` episodes (chat/voice answers) in the last
    ``days`` and, for each, checks the heuristics in order — re-ask >
    correction > acted-on > silence-after > neutral (first match wins, most
    specific/negative first so a real problem is never masked by a coincidental
    later action). ``config`` is accepted for a consistent call shape with the
    rest of the codebase; no setting is read from it yet.

    Returns a list of ``{episode_id, signal, reason, confidence}`` dicts, one
    per scored answer episode (dropping cache-hit episodes themselves — their
    signal is attributed to the ORIGINAL episode they re-served, see
    :data:`REASK_WINDOW_MINUTES`). An empty/degenerate log returns ``[]``.
    """
    try:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        ).isoformat()
        episodes = await _fetch_episodes(instance_id, cutoff)
        if not episodes:
            return []
        approvals = await _fetch_approvals(instance_id, cutoff)

        answer_eps = [e for e in episodes if (e.get("accion") or "") == ANSWER_ACCION]
        now = datetime.now(timezone.utc)

        out: list[dict] = []
        for i, ep in enumerate(answer_eps):
            ep_time = _parse_ts(ep.get("creado_en"))
            if ep_time is None:
                continue
            decision = ep.get("decision") or ""
            if _norm_text(decision).startswith("cache-hit"):
                # This episode IS a re-ask event for an earlier one — its own
                # signal is attributed to whichever earlier episode it re-served.
                continue

            # ── 1. re-ask ────────────────────────────────────────────────
            reask_hit = None
            reask_ratio = 0.0
            for later in answer_eps[i + 1 :]:
                later_time = _parse_ts(later.get("creado_en"))
                if later_time is None:
                    continue
                if later_time - ep_time > timedelta(minutes=REASK_WINDOW_MINUTES):
                    break
                is_dup, ratio = answers_match(ep.get("resultado"), later.get("resultado"))
                later_decision = _norm_text(later.get("decision"))
                if later_decision.startswith("cache-hit") or is_dup:
                    reask_hit = later
                    reask_ratio = ratio
                    break
            if reask_hit is not None:
                out.append(
                    _signal(
                        ep["id"],
                        "negative",
                        "re-ask: a near-duplicate/cache-served answer recurred "
                        f"shortly after (similarity={reask_ratio:.2f}) — the "
                        "question probably came back because the first answer "
                        "didn't land",
                        0.85 if reask_ratio >= 0.99 else 0.6,
                    )
                )
                continue

            # ── 2. correction ────────────────────────────────────────────
            correction_hit = None
            for later in answer_eps[i + 1 :]:
                later_time = _parse_ts(later.get("creado_en"))
                if later_time is None:
                    continue
                if later_time - ep_time > timedelta(minutes=CORRECTION_WINDOW_MINUTES):
                    break
                combined = f"{later.get('decision') or ''} {later.get('resultado') or ''}"
                if is_correction(combined):
                    correction_hit = later
                    break
            if correction_hit is not None:
                out.append(
                    _signal(
                        ep["id"],
                        "negative",
                        "correction: the following turn opened with a correction "
                        "phrase ('no'/'wrong'/'en realidad'/…)",
                        0.7,
                    )
                )
                continue

            # ── 3. acted-on ──────────────────────────────────────────────
            acted = False
            for later in episodes:
                later_time = _parse_ts(later.get("creado_en"))
                if later_time is None or later_time <= ep_time:
                    continue
                if later_time - ep_time > timedelta(minutes=ACTED_ON_WINDOW_MINUTES):
                    continue
                accion_later = (later.get("accion") or "").strip()
                if accion_later and accion_later != ANSWER_ACCION:
                    acted = True
                    break
            if not acted:
                for ap in approvals:
                    ap_time = _parse_ts(ap.get("creado_en"))
                    if ap_time is None or ap_time < ep_time:
                        continue
                    if ap_time - ep_time > timedelta(minutes=ACTED_ON_WINDOW_MINUTES):
                        continue
                    if ap.get("estado") == "aprobada":
                        acted = True
                        break
            if acted:
                out.append(
                    _signal(
                        ep["id"],
                        "positive",
                        "acted-on: a concrete action/approval followed shortly "
                        "after — the owner used the answer",
                        0.65,
                    )
                )
                continue

            # ── 4. silence-after ─────────────────────────────────────────
            next_answer = answer_eps[i + 1] if i + 1 < len(answer_eps) else None
            if next_answer is not None:
                next_time = _parse_ts(next_answer.get("creado_en"))
                gap = (next_time - ep_time) if next_time is not None else (now - ep_time)
            else:
                gap = now - ep_time
            if gap >= timedelta(minutes=SILENCE_WINDOW_MINUTES):
                out.append(
                    _signal(
                        ep["id"],
                        "positive",
                        "silence-after: no re-ask/correction followed within the "
                        "window — probably landed (weak: silence can also mean "
                        "the owner gave up)",
                        0.4,
                    )
                )
            else:
                out.append(
                    _signal(
                        ep["id"],
                        "neutral",
                        "inconclusive: not enough time has passed / no clear "
                        "signal yet",
                        0.3,
                    )
                )
        return out
    except Exception as exc:  # a $0 sense must never crash a caller
        logger.debug(f"feedback: derive_signals failed: {exc}")
        return []


async def summary(instance_id: str, *, days: int = DEFAULT_DAYS, config: Any = None) -> dict:
    """Aggregate :func:`derive_signals` into a cheap "am I landing?" health read.

    Returns ``{positive, negative, neutral, total, net_score, range_days}``.
    ``net_score`` is ``(positive - negative) / total`` in ``[-1, 1]``, or
    ``0.0`` when there is nothing to score yet. Never raises.
    """
    try:
        signals = await derive_signals(instance_id, days=days, config=config)
        positive = sum(1 for s in signals if s["signal"] == "positive")
        negative = sum(1 for s in signals if s["signal"] == "negative")
        neutral = sum(1 for s in signals if s["signal"] == "neutral")
        total = positive + negative + neutral
        net_score = round((positive - negative) / total, 4) if total else 0.0
        return {
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
            "total": total,
            "net_score": net_score,
            "range_days": int(days),
        }
    except Exception as exc:
        logger.debug(f"feedback: summary failed: {exc}")
        return {
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "total": 0,
            "net_score": 0.0,
            "range_days": int(days),
        }
