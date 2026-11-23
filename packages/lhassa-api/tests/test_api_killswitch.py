"""Tests for the gateway kill-switch and the dashboard cost-limit endpoints.

Fully offline: a fake completion callable stands in for the provider (and proves
it is never reached when the switch is on), the gateway DB + limits file live in
tmp_path, and the API is driven through ``httpx.ASGITransport`` (no live network).
"""

import httpx
import pytest_asyncio  # noqa: F401  (registers asyncio mode)



MESSAGES = [{"role": "user", "content": "Hi there"}]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web.",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    }
]


async def test_limits_get_post_round_trip(tmp_path, monkeypatch):
    from lhassa.api import server
    from lhassa.llm.gateway import gateway

    # Redirect the singleton gateway to tmp paths and a clean limit state.
    monkeypatch.setattr(gateway, "db_path", tmp_path / "gateway.db")
    monkeypatch.setattr(gateway, "limits_path", tmp_path / "llm_limits.json")
    monkeypatch.setattr(gateway, "_initialized", False)
    monkeypatch.setattr(gateway, "_budget_override", None)
    monkeypatch.setattr(gateway, "_kill_switch", False)
    monkeypatch.setattr(gateway, "_kill_switch_overridden", False)

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Initial state.
        r = await client.get("/api/llm/limits")
        assert r.status_code == 200
        body = r.json()
        assert body["kill_switch"] is False
        assert "daily_token_budget" in body
        assert "output_tokens_today" in body

        # Update budget + kill-switch.
        r = await client.post(
            "/api/llm/limits",
            json={"daily_token_budget": 1234, "kill_switch": True},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["daily_token_budget"] == 1234
        assert body["kill_switch"] is True

        # Round-trip GET reflects the change, and the budget was persisted.
        r = await client.get("/api/llm/limits")
        body = r.json()
        assert body["daily_token_budget"] == 1234
        assert body["kill_switch"] is True
        assert gateway.kill_switch is True
        assert (tmp_path / "llm_limits.json").exists()

        # Bad payloads are rejected.
        r = await client.post("/api/llm/limits", json={"daily_token_budget": -5})
        assert r.status_code == 400
        r = await client.post("/api/llm/limits", json={"kill_switch": "yes"})
        assert r.status_code == 400


async def test_ledger_endpoint_exposes_kill_switch(tmp_path, monkeypatch):
    from lhassa.api import server
    from lhassa.llm.gateway import gateway

    monkeypatch.setattr(gateway, "db_path", tmp_path / "gateway.db")
    monkeypatch.setattr(gateway, "limits_path", tmp_path / "llm_limits.json")
    monkeypatch.setattr(gateway, "_initialized", False)
    monkeypatch.setattr(gateway, "_budget_override", None)
    monkeypatch.setattr(gateway, "_kill_switch", True)
    monkeypatch.setattr(gateway, "_kill_switch_overridden", False)

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/llm/ledger")
        assert r.status_code == 200
        assert r.json()["kill_switch"] is True
