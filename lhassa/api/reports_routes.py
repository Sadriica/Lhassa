"""Reports / projections routes — Phase-6 read-only dashboard surfaces.

Rich dashboard surfaces for "madurez": what the brain has been doing
(``/api/reports`` — episodes summary) and where cost is trending
(``/api/projections`` — a linear projection off the same ledger/ccusage as
``/api/llm/*``). Both read-only and instance-scoped.

``Config`` is read through the ``lhassa.config`` MODULE (``lhassa_config.Config``)
rather than a bound name, so a config reload (some endpoint tests reload it) is
picked up live — the same seam ``runtime_routes`` uses.

Mounted by the integrator with ``app.include_router(reports_routes.router)``.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Query

import lhassa.config as lhassa_config
from lhassa.core import db

router = APIRouter()


@router.get("/api/reports")
async def get_reports(n: int = Query(default=50, ge=1, le=500)):
    """Recent episodes + an outcomes summary (counts by wake trigger / decision)."""
    await db.init_db()
    rows = await db.load_recent_episodes(lhassa_config.Config.instance_id, n)

    by_despertar: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    for r in rows:
        trigger = str(r.get("despertar_por") or "?").split(":", 1)[0]
        by_despertar[trigger] = by_despertar.get(trigger, 0) + 1
        decision = str(r.get("decision") or "?")
        by_decision[decision] = by_decision.get(decision, 0) + 1

    recent = [
        {
            "id": r.get("id"),
            "creado_en": r.get("creado_en"),
            "despertar_por": r.get("despertar_por"),
            "decision": r.get("decision"),
            "accion": r.get("accion"),
            "resultado": r.get("resultado"),
            "dominio": r.get("dominio"),
            "costo_usd": r.get("costo_usd"),
        }
        for r in rows
    ]
    return {
        "count": len(rows),
        "by_despertar": by_despertar,
        "by_decision": by_decision,
        "recent": recent,
    }


@router.get("/api/projections")
async def get_projections():
    """Cost projection from the gateway ledger (same data as /api/llm/*).

    Spend so far today + a simple linear projection to day/month end. Pricing is
    NOT duplicated — the ledger already carries per-call ``cost_usd``. The
    cross-harness ccusage read is included (defensive; never blocks)."""
    from lhassa.llm.ccusage import read_ccusage
    from lhassa.llm.gateway import gateway

    config = lhassa_config.Config()
    rows = await gateway.ledger_rows()

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    today_cost = 0.0
    today_in = 0
    today_out = 0
    for r in rows:
        if str(r.get("ts") or "").startswith(today):
            today_cost += float(r.get("cost_usd") or 0.0)
            today_in += int(r.get("input_tokens") or 0)
            today_out += int(r.get("output_tokens") or 0)

    seconds_elapsed = now.hour * 3600 + now.minute * 60 + now.second
    day_fraction = max(seconds_elapsed / 86400.0, 1e-6)
    projected_today = round(today_cost / day_fraction, 6)

    return {
        "today": {
            "date": today,
            "cost_usd": round(today_cost, 6),
            "input_tokens": today_in,
            "output_tokens": today_out,
        },
        "budget": {
            "daily_token_budget": gateway.effective_daily_budget(config),
            "output_tokens_today": await gateway.daily_output_tokens(),
        },
        "projection": {
            "day_fraction_elapsed": round(day_fraction, 4),
            "projected_today_cost_usd": projected_today,
            "projected_month_cost_usd": round(projected_today * 30, 6),
        },
        "ccusage": await read_ccusage(),
    }
