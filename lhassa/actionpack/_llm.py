# SPDX-License-Identifier: AGPL-3.0-or-later
"""LLM spend visibility action: check_spend ("spend as a signal")."""

from __future__ import annotations


async def check_spend(params: dict) -> str:
    """Report today's LLM spend as a short, human-readable summary line.

    Wraps ``lhassa.llm.gateway.spend_summary()`` — today's output tokens, the
    effective daily budget (0 = unlimited), the fraction of that budget used,
    and today's real cost in USD. Deterministic and $0 itself (it only reads the
    ledger, it never calls a model). Never raises.

    params: (none required)
    """
    try:
        from lhassa.llm.gateway import spend_summary

        summary = await spend_summary()
        budget = summary.get("budget") or 0
        budget_str = str(budget) if budget else "unlimited"
        pct = summary.get("pct_used") or 0.0
        return (
            f"check_spend: {summary.get('output_tokens', 0)} output tokens today "
            f"(budget {budget_str}, {pct:.0%} used), cost ${summary.get('cost_usd', 0.0):.4f}"
        )
    except Exception as exc:
        return f"check_spend: error: {exc}"
