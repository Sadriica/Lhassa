"""Tests for lhassa.llm.ccusage — cross-harness accounting from the ccusage CLI.

Fully offline: ``parse_ccusage`` is exercised on inline sample fixtures, and
``read_ccusage`` is tested with the subprocess launcher monkeypatched to raise
FileNotFoundError (binary absent). No real ccusage / npx / network is invoked.
"""

import httpx
import pytest
import pytest_asyncio  # noqa: F401  (registers asyncio mode)

from lhassa.llm import ccusage
from lhassa.llm.ccusage import parse_ccusage, read_ccusage


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


def test_parse_totals_shape_single_harness():
    out = parse_ccusage(SAMPLE_TOTALS, source="ccusage")
    assert out["available"] is True
    assert set(out["by_harness"]) == {"ccusage"}
    assert out["by_harness"]["ccusage"]["cost_usd"] == pytest.approx(0.42)
    assert out["by_harness"]["ccusage"]["tokens"] == 4300
    assert out["total_cost_usd"] == pytest.approx(0.42)
    assert out["source"] == "ccusage"


def test_parse_harness_breakdown():
    out = parse_ccusage(SAMPLE_HARNESSES)
    assert out["available"] is True
    assert set(out["by_harness"]) == {"claude-code", "codex", "opencode"}
    assert out["by_harness"]["codex"]["cost_usd"] == pytest.approx(0.05)
    # opencode has no totalTokens → input+output are summed.
    assert out["by_harness"]["opencode"]["tokens"] == 400
    assert out["total_cost_usd"] == pytest.approx(0.47)


def test_parse_unexpected_is_unavailable():
    assert parse_ccusage(["not", "a", "dict"])["available"] is False
    assert parse_ccusage({})["available"] is False
    assert parse_ccusage({"random": "shape"})["available"] is False
    # Never raises and always carries the normalized keys.
    out = parse_ccusage(None)
    assert out["by_harness"] == {}
    assert out["total_cost_usd"] == 0.0


# ── read_ccusage ──────────────────────────────────────────────────────────────


async def test_read_ccusage_missing_binary_is_unavailable(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("ccusage: not found")

    monkeypatch.setattr(ccusage.asyncio, "create_subprocess_exec", boom)

    out = await read_ccusage()
    assert out["available"] is False
    assert out["by_harness"] == {}
    assert out["total_cost_usd"] == 0.0


async def test_read_ccusage_parses_subprocess_output(monkeypatch):
    import json

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return json.dumps(SAMPLE_TOTALS).encode("utf-8"), b""

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(ccusage.asyncio, "create_subprocess_exec", fake_exec)

    out = await read_ccusage()
    assert out["available"] is True
    assert out["total_cost_usd"] == pytest.approx(0.42)


async def test_read_ccusage_bad_json_is_unavailable(monkeypatch):
    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"not json at all", b""

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(ccusage.asyncio, "create_subprocess_exec", fake_exec)

    out = await read_ccusage()
    assert out["available"] is False


# ── /api/llm/ccusage endpoint ─────────────────────────────────────────────────


async def test_ccusage_endpoint_defensive(monkeypatch):
    from lhassa.api import server

    async def fake_read(timeout=None):
        return {"available": False, "by_harness": {}, "total_cost_usd": 0.0, "source": "ccusage"}

    monkeypatch.setattr("lhassa.llm.ccusage.read_ccusage", fake_read)

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/llm/ccusage")
        assert r.status_code == 200
        assert r.json()["available"] is False
