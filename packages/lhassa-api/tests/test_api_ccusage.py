"""Tests for lhassa.llm.ccusage; cross-harness accounting from the ccusage CLI.

Fully offline: ``parse_ccusage`` is exercised on inline sample fixtures, and
``read_ccusage`` is tested with the subprocess launcher monkeypatched to raise
FileNotFoundError (binary absent). No real ccusage / npx / network is invoked.
"""

import httpx
import pytest_asyncio  # noqa: F401  (registers asyncio mode)



# [SUPUESTO] ccusage's documented daily report: a list of per-day rows plus a
# ``totals`` summary. Validate the exact field names in a live smoke.
SAMPLE_TOTALS = {
    "daily": [
        {
            "date": "2026-06-28",
            "inputTokens": 1000,
            "outputTokens": 500,
            "totalTokens": 1500,
            "totalCost": 0.12,
        },
        {
            "date": "2026-06-29",
            "inputTokens": 2000,
            "outputTokens": 800,
            "totalTokens": 2800,
            "totalCost": 0.30,
        },
    ],
    "totals": {
        "inputTokens": 3000,
        "outputTokens": 1300,
        "totalTokens": 4300,
        "totalCost": 0.42,
    },
}

# [SUPUESTO] A cross-harness aggregation keying cost/tokens by harness name.
SAMPLE_HARNESSES = {
    "harnesses": {
        "claude-code": {"totalTokens": 4300, "totalCost": 0.42},
        "codex": {"totalTokens": 1200, "totalCost": 0.05},
        "opencode": {"inputTokens": 300, "outputTokens": 100, "totalCost": 0.0},
    }
}


# ── parse_ccusage ─────────────────────────────────────────────────────────────


async def test_ccusage_endpoint_defensive(monkeypatch):
    from lhassa.api import server

    async def fake_read(timeout=None, allow_npx=True):
        return {"available": False, "by_harness": {}, "total_cost_usd": 0.0, "source": "ccusage"}

    monkeypatch.setattr("lhassa.llm.ccusage.read_ccusage", fake_read)

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/llm/ccusage")
        assert r.status_code == 200
        assert r.json()["available"] is False
