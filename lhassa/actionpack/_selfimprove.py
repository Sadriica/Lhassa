# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-improvement actions: detect_repetition, review_tools (§2.8, Etapa 4).

These close the auto-mejora loop end to end. Both are deterministic and $0;
the underlying detection/gate logic (agents.repetition / agents.gates) never
calls an LLM; only a human resolving the resulting approval (or a later,
explicit ``lhassa tools propose``) ever spends anything.
"""

from __future__ import annotations

from lhassa.agents import gates as _gates
from lhassa.agents import repetition as _repetition
from lhassa.config import Config
from lhassa.core import db as _db
from lhassa.notify.dispatch import notify as _notify
from lhassa.security import approval as _approval

# watch_state key prefix: a repetition pattern is proposed only ONCE, not on
# every subsequent run; mirrors the watchlist transition-dedup idiom.
_SEEN_PREFIX = "repetition:seen:"

# Per-sweep ceiling per proposal type. Together with the pending cap
# (Config.approval_pending_cap, read through approval.pending_count) it bounds
# how much one sweep can add to the queue: 37 + 13 unanswered proposals were
# once parked in a few weeks, all rejected.
_MAX_PER_TYPE_PER_RUN = 5

# A remembered answer is only worth proposing if the pattern recurs on at least
# this many distinct days: one burst (16 episodes in 8 minutes) is a session,
# not a habit.
_MIN_MEMORY_DAYS = 2


class _Room:
    """How many more approvals of each type this sweep may park."""

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self._left: dict = {}

    async def allow(self, tipo: str) -> bool:
        if tipo not in self._left:
            left = _MAX_PER_TYPE_PER_RUN
            cap = int(getattr(Config, "approval_pending_cap", 0) or 0)
            if cap > 0:
                left = min(left, cap - await _approval.pending_count(self.instance_id, tipo))
            self._left[tipo] = left
        return self._left[tipo] > 0

    def used(self, tipo: str) -> None:
        self._left[tipo] = self._left.get(tipo, 0) - 1


async def _seen_chain_patterns(instance_id: str) -> list:
    """Chains already proposed (any rotation), as ``(despertar_por, chain)`` pairs."""
    from lhassa._vendor import aiosqlite

    out: list = []
    try:
        async with aiosqlite.connect(_db.DB_PATH) as conn:
            async with conn.execute(
                "SELECT key FROM watch_state WHERE instance_id = ? AND key LIKE ?",
                (instance_id, _SEEN_PREFIX + "%::chain:%"),
            ) as cur:
                for (key,) in await cur.fetchall():
                    parsed = _repetition._parse_chain_pattern(str(key)[len(_SEEN_PREFIX):])
                    if parsed:
                        out.append(parsed)
    except Exception:
        return []
    return out


async def detect_repetition(params: dict) -> str:
    """Detect repeated judgment patterns and propose minting a deterministic action.

    Runs ``agents.repetition.detect_repetition`` (pure SQL, $0). For each NEW
    pattern (deduped via ``watch_state`` so a proposal is parked only once)
    parks a ``mint_action`` approval titled "mint deterministic action for
    <pattern>" AND sends a short notification. Never raises; never calls an LLM.

    params:
        instance_id ; defaults to Config.instance_id.
        days        ; lookback window in days (default 7).
    """
    try:
        instance_id = params.get("instance_id") or Config.instance_id
        days_raw = params.get("days")
        days = int(days_raw) if days_raw is not None else 7

        await _db.init_db()
        findings = await _repetition.detect_repetition(instance_id, days=days)

        proposed = 0
        room = _Room(instance_id)
        for f in findings:
            key = _SEEN_PREFIX + f["pattern"]
            if await _db.get_watch_state(instance_id, key):
                continue  # already proposed this exact pattern once
            if not await room.allow("mint_action"):
                break  # sweep ceiling or pending cap reached; the rest waits

            title = f"mint deterministic action for {f['pattern']}"
            payload = {
                "accion": "mint_action",
                "pattern": f["pattern"],
                "despertar_por": f["despertar_por"],
                "candidate_accion": f["accion"],
                "count": f["count"],
                "total_cost_usd": f["total_cost_usd"],
                "title": title,
            }
            await _approval.request_approval(
                instance_id, tipo="mint_action", payload=payload
            )
            await _notify(
                f"{title}; repeated {f['count']}x in the last {days}d, "
                f"cost ${f['total_cost_usd']:.4f}",
                level="info",
            )
            await _db.set_watch_state(instance_id, key, "1")
            room.used("mint_action")
            proposed += 1

        # Second signal: repeated MECHANICAL action chains → mint_script (PTC)
        # proposals. Same once-only dedupe; the payload carries a deterministic
        # draft the human reviews (running it still crosses the approval gate).
        chains = await _repetition.detect_mechanical_chains(instance_id, days=days)
        seen_chains = await _seen_chain_patterns(instance_id)
        for f in chains:
            key = _SEEN_PREFIX + f["pattern"]
            if await _db.get_watch_state(instance_id, key):
                continue
            # A rotation, repetition or sub-chain of a chain already proposed
            # (under an older, non-canonical key too) is the SAME proposal.
            dp = f["despertar_por"]
            canon = _repetition._min_rotation(f["chain"])
            if any(
                sdp == dp
                and (
                    _repetition._min_rotation(sc) == canon
                    or _repetition.chain_covers(canon, sc)
                )
                for sdp, sc in seen_chains
            ):
                continue
            if not await room.allow("mint_script"):
                break
            chain_label = " → ".join(f["chain"])
            payload = {
                "accion": "mint_script",
                "pattern": f["pattern"],
                "despertar_por": f["despertar_por"],
                "chain": f["chain"],
                "count": f["count"],
                "days_seen": f.get("days_seen", 0),
                "draft_script": _repetition.draft_script_for_chain(f["chain"]),
                "title": f"collapse the {chain_label} chain into one script",
            }
            await _approval.request_approval(
                instance_id, tipo="mint_script", payload=payload
            )
            await _notify(
                f"PTC candidate: {chain_label} repeated {f['count']}x in the "
                f"last {days}d; a draft script awaits your review.",
                level="info",
            )
            await _db.set_watch_state(instance_id, key, "1")
            seen_chains.append((dp, tuple(f["chain"])))
            room.used("mint_script")
            proposed += 1

        # Third signal: the consciousness; recurring patterns (any cost,
        # temporal cadence, cache-worthy repeated questions) reasoned through
        # recommend_intervention into the cheapest of mint_memory / mint_schedule
        # / seed_cache. "tool" and "script" recommendations are skipped here;
        # those exact patterns are already covered by the two loops above, so
        # proposing them again would just duplicate the same fix.
        all_recurring = await _repetition.detect_recurring(instance_id, days=days)
        # Only the kinds this loop actually owns count as a distinct signal;
        # "tool"/"script" recommendations are the SAME fix the two loops above
        # already proposed for that exact pattern, not a new finding.
        recurring = [
            (f, _repetition.recommend_intervention(f))
            for f in all_recurring
        ]
        recurring = [(f, rec) for f, rec in recurring if rec["kind"] not in ("tool", "script")]

        for f, rec in recurring:
            key = _SEEN_PREFIX + f["pattern"]
            if await _db.get_watch_state(instance_id, key):
                continue

            kind = rec["kind"]
            tipo = {
                "memory_fact": "mint_memory",
                "schedule": "mint_schedule",
                "cache_seed": "seed_cache",
            }[kind]
            if kind == "memory_fact" and not (
                rec["payload"].get("answer")
                and int(rec["payload"].get("days_seen") or 0) >= _MIN_MEMORY_DAYS
            ):
                # Nothing storable to approve, or a single-session burst: a
                # mint_memory the owner cannot judge or act on is queue noise.
                continue
            if not await room.allow(tipo):
                continue
            payload = dict(rec["payload"])
            payload["accion"] = tipo
            payload["rationale"] = rec["rationale"]

            if kind == "memory_fact":
                title = f"remember the answer for '{f['despertar_por']}'"
            elif kind == "schedule":
                title = f"schedule '{f['despertar_por']}' on its own cadence"
            else:
                subject = f.get("question") or f["despertar_por"]
                title = f"'{subject}' is already answered from cache"
            payload["title"] = title

            await _approval.request_approval(instance_id, tipo=tipo, payload=payload)
            await _notify(f"{title}; {rec['rationale']}", level="info")
            await _db.set_watch_state(instance_id, key, "1")
            room.used(tipo)
            proposed += 1

        # Fourth signal: the self-routing consciousness; a message class a
        # deterministic capability already recognises (see
        # lhassa.notify.capabilities) but that still paid for a think/research
        # because no route is on yet. Same once-only dedupe; approving turns
        # DATA on (adds a route-table entry), never generates code.
        routable = await _repetition.detect_routable(instance_id, days=days)
        for f in routable:
            key = _SEEN_PREFIX + f["pattern"]
            if await _db.get_watch_state(instance_id, key):
                continue

            rec = _repetition.recommend_intervention(f)
            if rec["kind"] != "route":
                continue  # a malformed finding degraded elsewhere; skip it

            if not await room.allow("mint_route"):
                break
            payload = dict(rec["payload"])
            payload["accion"] = "mint_route"
            payload["rationale"] = rec["rationale"]
            title = f"Route {f['capability']} questions to the $0 handler"
            payload["title"] = title

            await _approval.request_approval(
                instance_id, tipo="mint_route", payload=payload
            )
            await _notify(f"{title}; {rec['rationale']}", level="info")
            await _db.set_watch_state(instance_id, key, "1")
            room.used("mint_route")
            proposed += 1

        total = len(findings) + len(chains) + len(recurring) + len(routable)
        if not total:
            return "detect_repetition: no repeated patterns found"
        return (
            f"detect_repetition: {total} pattern(s) found, "
            f"{proposed} newly proposed"
        )

    except Exception as exc:  # an action must NEVER raise
        return f"detect_repetition: error: {exc}"


async def review_tools(params: dict) -> str:
    """Gate 4 (Retiro): retire graduated tools that went unused or fail too often.

    Iterates every tool row for this instance, applies
    ``agents.gates.should_retire``, and calls ``agents.gates.retire`` on any
    flagged tool (demoting it back to cuarentena). Never raises; never calls
    an LLM.

    params:
        instance_id ; defaults to Config.instance_id.
    """
    try:
        instance_id = params.get("instance_id") or Config.instance_id
        await _db.init_db()
        rows = await _db.list_tools(instance_id)

        retired: list[str] = []
        for row in rows:
            if not _gates.should_retire(row):
                continue
            nombre = row.get("nombre") or ""
            if not nombre:
                continue
            await _gates.retire(
                instance_id, nombre, reason="gate 4: unused or high failure rate"
            )
            retired.append(nombre)

        if not retired:
            return f"review_tools: {len(rows)} tool(s) checked, none retired"
        return (
            f"review_tools: retired {len(retired)}/{len(rows)}: "
            + ", ".join(retired)
        )

    except Exception as exc:  # an action must NEVER raise
        return f"review_tools: error: {exc}"
