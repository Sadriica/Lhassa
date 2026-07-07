"""Self-improvement actions: detect_repetition, review_tools (§2.8, Etapa 4).

These close the auto-mejora loop end to end. Both are deterministic and $0 —
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
# every subsequent run — mirrors the watchlist transition-dedup idiom.
_SEEN_PREFIX = "repetition:seen:"


async def detect_repetition(params: dict) -> str:
    """Detect repeated judgment patterns and propose minting a deterministic action.

    Runs ``agents.repetition.detect_repetition`` (pure SQL, $0). For each NEW
    pattern (deduped via ``watch_state`` so a proposal is parked only once)
    parks a ``mint_action`` approval titled "mint deterministic action for
    <pattern>" AND sends a short notification. Never raises; never calls an LLM.

    params:
        instance_id  — defaults to Config.instance_id.
        days         — lookback window in days (default 7).
    """
    try:
        instance_id = params.get("instance_id") or Config.instance_id
        days_raw = params.get("days")
        days = int(days_raw) if days_raw is not None else 7

        await _db.init_db()
        findings = await _repetition.detect_repetition(instance_id, days=days)

        proposed = 0
        for f in findings:
            key = _SEEN_PREFIX + f["pattern"]
            if await _db.get_watch_state(instance_id, key):
                continue  # already proposed this exact pattern once

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
                f"{title} — repeated {f['count']}x in the last {days}d, "
                f"cost ${f['total_cost_usd']:.4f}",
                level="info",
            )
            await _db.set_watch_state(instance_id, key, "1")
            proposed += 1

        # Second signal: repeated MECHANICAL action chains → mint_script (PTC)
        # proposals. Same once-only dedupe; the payload carries a deterministic
        # draft the human reviews (running it still crosses the approval gate).
        chains = await _repetition.detect_mechanical_chains(instance_id, days=days)
        for f in chains:
            key = _SEEN_PREFIX + f["pattern"]
            if await _db.get_watch_state(instance_id, key):
                continue
            chain_label = " → ".join(f["chain"])
            payload = {
                "accion": "mint_script",
                "pattern": f["pattern"],
                "despertar_por": f["despertar_por"],
                "chain": f["chain"],
                "count": f["count"],
                "draft_script": _repetition.draft_script_for_chain(f["chain"]),
                "title": f"collapse the {chain_label} chain into one script",
            }
            await _approval.request_approval(
                instance_id, tipo="mint_script", payload=payload
            )
            await _notify(
                f"PTC candidate: {chain_label} repeated {f['count']}x in the "
                f"last {days}d — a draft script awaits your review.",
                level="info",
            )
            await _db.set_watch_state(instance_id, key, "1")
            proposed += 1

        # Third signal: the consciousness — recurring patterns (any cost,
        # temporal cadence, cache-worthy repeated questions) reasoned through
        # recommend_intervention into the cheapest of mint_memory / mint_schedule
        # / seed_cache. "tool" and "script" recommendations are skipped here —
        # those exact patterns are already covered by the two loops above, so
        # proposing them again would just duplicate the same fix.
        all_recurring = await _repetition.detect_recurring(instance_id, days=days)
        # Only the kinds this loop actually owns count as a distinct signal —
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
            await _notify(f"{title} — {rec['rationale']}", level="info")
            await _db.set_watch_state(instance_id, key, "1")
            proposed += 1

        total = len(findings) + len(chains) + len(recurring)
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
        instance_id  — defaults to Config.instance_id.
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
