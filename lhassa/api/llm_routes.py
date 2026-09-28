# SPDX-License-Identifier: AGPL-3.0-or-later
"""LLM cost routes; the gateway ledger, runtime limits, and cross-harness ccusage.

The dashboard's cost surface. Reads the cost-gateway ledger (per-call tokens/cost),
exposes + updates the runtime daily token budget and the kill-switch, and folds in
the ``ccusage`` CLI's cross-harness subscription accounting (defensive; never blocks).

Mounted by the integrator with ``app.include_router(llm_routes.router)``.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from lhassa.state import state
from lhassa.config import Config

router = APIRouter()


@router.get("/api/llm/ledger")
async def get_llm_ledger():
    from lhassa.llm.gateway import gateway

    config = Config()
    rows = await gateway.ledger_rows()
    recent = list(reversed(rows[-20:]))  # newest first
    output_tokens_today = await gateway.daily_output_tokens()

    # Real, persistent totals from the ledger DB (same source as /api/overview
    # and /api/projections), summed for today (UTC). In-memory state.metrics
    # resets on restart, which made this panel disagree with the others.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    calls = 0
    input_tokens = 0
    output_tokens = 0
    cost_usd = 0.0
    cache_hits = 0
    for r in rows:
        if str(r.get("ts") or "").startswith(today):
            calls += 1
            input_tokens += int(r.get("input_tokens") or 0)
            output_tokens += int(r.get("output_tokens") or 0)
            cost_usd += float(r.get("cost_usd") or 0.0)
            cache_hits += int(r.get("cache_hit") or 0)
    totals = {
        "llm_calls": calls,
        "llm_input_tokens": input_tokens,
        "llm_output_tokens": output_tokens,
        "llm_cost_usd": round(cost_usd, 6),
        "llm_cache_hits": cache_hits,
        "period": "today",
    }

    return {
        "totals": totals,
        "budget": {
            "daily_token_budget": gateway.effective_daily_budget(config),
            "output_tokens_today": output_tokens_today,
        },
        "kill_switch": gateway.kill_switch,
        "cache_enabled": config.llm_cache_enabled,
        "recent": recent,
    }


@router.get("/api/llm/limits")
async def get_llm_limits():
    """Current cost limits: the effective daily token budget, the kill-switch
    state, and how many output tokens have been spent today (UTC)."""
    from lhassa.llm.gateway import gateway

    config = Config()
    return {
        "daily_token_budget": gateway.effective_daily_budget(config),
        "kill_switch": gateway.kill_switch,
        "output_tokens_today": await gateway.daily_output_tokens(),
    }


@router.post("/api/llm/limits")
async def set_llm_limits(payload: dict):
    """Update the runtime daily token budget and/or the kill-switch.

    Body: ``{daily_token_budget?: int >= 0, kill_switch?: bool}``. The budget is
    persisted so it survives a restart; the kill-switch is in-session. Returns the
    same shape as ``GET /api/llm/limits``.
    """
    from lhassa.llm.gateway import gateway

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="limits payload must be a JSON object.")

    if "daily_token_budget" in payload:
        budget = payload["daily_token_budget"]
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise HTTPException(
                status_code=400,
                detail="daily_token_budget must be a non-negative integer (0 = unlimited).",
            )
        gateway.set_daily_token_budget(budget)

    if "kill_switch" in payload:
        kill_switch = payload["kill_switch"]
        if not isinstance(kill_switch, bool):
            raise HTTPException(status_code=400, detail="kill_switch must be a boolean.")
        gateway.set_kill_switch(kill_switch)

    config = Config()
    return {
        "daily_token_budget": gateway.effective_daily_budget(config),
        "kill_switch": gateway.kill_switch,
        "output_tokens_today": await gateway.daily_output_tokens(),
    }


@router.get("/api/llm/ccusage")
async def get_llm_ccusage():
    """Cross-harness accounting from the ``ccusage`` CLI (Claude Code / Codex /
    opencode subscription usage). Defensive: returns ``available: False`` when the
    tool is absent or errors; never blocks the dashboard."""
    from lhassa.llm.ccusage import read_ccusage

    # Bounded budget: enough for the ~3-4s offline ccusage read (real data), but never
    # the slow npx cold-start; degrade fast when the binary is absent.
    return await read_ccusage(timeout=6.0, allow_npx=False)
