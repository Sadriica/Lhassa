# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning tokens into money, honestly.

Most hosts report tokens and not dollars, so someone has to multiply. The
danger is that a made-up price looks exactly like a real one in a report, so
this module has one hard rule: **if the price is not known, the cost is None
and the source says `unknown`.** A blank is auditable. A guess is not.

Prices live in a JSON file the owner controls (`AGENT_LEDGER_PRICES`, default
`~/.agent-ledger/prices.json`) so that correcting a rate never means shipping
a release. The built-in table is a small starting point, not a source of
truth, and every entry names the date it was written.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from .event import COST_FROM_TABLE, COST_UNKNOWN

logger = logging.getLogger(__name__)

PRICES_PATH = Path(
    os.environ.get("AGENT_LEDGER_PRICES", Path.home() / ".agent-ledger" / "prices.json")
)

# USD per million tokens. Matched by longest substring against the model name,
# so "deepseek-v4-flash" wins over "deepseek". Written 2026-08-30; treat as a
# seed and override in prices.json rather than editing here.
BUILTIN_PRICES: dict[str, dict[str, float]] = {
    "deepseek-v4-flash": {"input": 0.07, "output": 0.28},
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
}

# A subscription seat is not free, but the marginal call is. Charging it per
# token would double count what the owner already paid, so these report a
# real zero rather than an unknown.
FLAT_RATE_HOSTS = frozenset({"claude-code", "codex", "opencode"})

# Routers mark their no-charge tiers in the model name itself (OpenRouter's
# ":free", and the same convention downstream). That is a real zero, not an
# unknown price, and saying so keeps the free tier out of the unpriced count.
FREE_MODEL_SUFFIXES = (":free",)


def _load_overrides() -> dict[str, dict[str, float]]:
    try:
        if PRICES_PATH.exists():
            data = json.loads(PRICES_PATH.read_text())
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        logger.warning("agent-ledger: could not read %s", PRICES_PATH, exc_info=True)
    return {}


def price_for(model: str) -> Optional[dict[str, float]]:
    """The rate for a model, or None when nothing credible matches."""
    if not model:
        return None
    table = {**BUILTIN_PRICES, **_load_overrides()}
    matches = [key for key in table if key and key in model]
    if not matches:
        return None
    return table[max(matches, key=len)]


def cost_of(
    *,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    host: str = "",
    reported_cost: Optional[float] = None,
) -> tuple[Optional[float], str]:
    """Return (cost_usd, cost_source).

    A cost the host reported always wins: it is the charge that will actually
    appear on a bill, and our arithmetic is only an estimate of it.
    """
    if reported_cost is not None:
        from .event import COST_FROM_HOST
        return float(reported_cost), COST_FROM_HOST

    if host in FLAT_RATE_HOSTS:
        # Billed by subscription; the marginal token costs nothing extra.
        return 0.0, COST_FROM_TABLE

    if model and model.endswith(FREE_MODEL_SUFFIXES):
        return 0.0, COST_FROM_TABLE

    rate = price_for(model)
    if rate is None:
        return None, COST_UNKNOWN

    per_million = (
        input_tokens * rate.get("input", 0.0)
        + output_tokens * rate.get("output", 0.0)
        + cache_read_tokens * rate.get("cache_read", rate.get("input", 0.0))
        + cache_write_tokens * rate.get("cache_write", rate.get("input", 0.0))
    )
    return round(per_million / 1_000_000, 8), COST_FROM_TABLE
