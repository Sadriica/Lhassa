"""Tests for the gateway kill-switch and the dashboard cost-limit endpoints.

Fully offline: a fake completion callable stands in for the provider (and proves
it is never reached when the switch is on), the gateway DB + limits file live in
tmp_path, and the API is driven through ``httpx.ASGITransport`` (no live network).
"""

import httpx
import pytest
import pytest_asyncio  # noqa: F401  (registers asyncio mode)

from lhassa.llm.gateway import (
    BudgetExceeded,
    KillSwitchActive,
    LedgerGateway,
)
from lhassa.state import state


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


class FakeConfig:
    def __init__(self, *, llm_provider="anthropic", model="claude-opus-4-8-20251101",
                 llm_cache_enabled=True, llm_daily_token_budget=0):
        self.llm_provider = llm_provider
        self.model = model
        self.llm_cache_enabled = llm_cache_enabled
        self.llm_daily_token_budget = llm_daily_token_budget


def make_fake_completion(text="hello", input_tokens=100, output_tokens=50):
    calls = {"n": 0}

    async def fake(provider, model, messages, max_tokens, temperature, system):
        calls["n"] += 1
        return text, input_tokens, output_tokens

    return fake, calls


def make_fake_tools_completion(text="done", tool_calls=None, input_tokens=100,
                               output_tokens=50, stop_reason="end_turn"):
    calls = {"n": 0}

    async def fake(provider, model, messages, tools, system, max_tokens):
        calls["n"] += 1
        return text, list(tool_calls or []), input_tokens, output_tokens, stop_reason

    return fake, calls


@pytest.fixture
def gw(tmp_path):
    """A gateway with its DB + limits file in throwaway tmp paths (never ~/.lhassa)."""
    return LedgerGateway(
        db_path=tmp_path / "gateway.db",
        limits_path=tmp_path / "llm_limits.json",
    )


@pytest.fixture
def metrics_guard():
    saved = dict(state.metrics)
    yield
    state.metrics.clear()
    state.metrics.update(saved)


# ── Kill-switch semantics ────────────────────────────────────────────────────


def test_kill_switch_is_a_budget_subclass():
    # Existing degrade paths catch BudgetExceeded — the kill-switch must too.
    assert issubclass(KillSwitchActive, BudgetExceeded)


def test_default_off_and_runtime_setter(gw):
    assert gw.kill_switch is False
    gw.set_kill_switch(True)
    assert gw.kill_switch is True
    gw.set_kill_switch(False)
    assert gw.kill_switch is False


def test_env_default_engages(tmp_path, monkeypatch):
    monkeypatch.setenv("LHASSA_KILL_SWITCH", "1")
    g = LedgerGateway(db_path=tmp_path / "g.db", limits_path=tmp_path / "lim.json")
    assert g.kill_switch is True


async def test_complete_refused_when_switch_on(gw, metrics_guard):
    cfg = FakeConfig()
    fake, calls = make_fake_completion()
    gw.set_kill_switch(True)

    with pytest.raises(KillSwitchActive):
        await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    # The provider was NEVER reached.
    assert calls["n"] == 0


async def test_complete_with_tools_refused_when_switch_on(gw, metrics_guard):
    cfg = FakeConfig()
    fake, calls = make_fake_tools_completion(tool_calls=[])
    gw.set_kill_switch(True)

    with pytest.raises(KillSwitchActive):
        await gw.complete_with_tools(MESSAGES, TOOLS, config=cfg, completion_fn=fake)
    assert calls["n"] == 0


async def test_releasing_switch_restores_normal_behaviour(gw, metrics_guard):
    cfg = FakeConfig(llm_cache_enabled=False)
    fake, calls = make_fake_completion(text="ok")

    gw.set_kill_switch(True)
    with pytest.raises(KillSwitchActive):
        await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert calls["n"] == 0

    gw.set_kill_switch(False)
    result = await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert result.text == "ok"
    assert calls["n"] == 1


# ── Runtime budget override (persisted) ──────────────────────────────────────


async def test_budget_override_enforced_and_persisted(tmp_path, metrics_guard):
    limits_path = tmp_path / "llm_limits.json"
    g = LedgerGateway(db_path=tmp_path / "g.db", limits_path=limits_path)
    cfg = FakeConfig(llm_daily_token_budget=0, llm_cache_enabled=False)

    # Override to 50; one call spends 50, the next is refused.
    g.set_daily_token_budget(50)
    assert g.effective_daily_budget(cfg) == 50
    assert limits_path.exists()  # persisted

    fake, _ = make_fake_completion(output_tokens=50)
    await g.complete(MESSAGES, config=cfg, completion_fn=fake)
    with pytest.raises(BudgetExceeded):
        await g.complete(MESSAGES, config=cfg, completion_fn=fake)

    # A fresh gateway pointed at the same limits file picks the override back up.
    g2 = LedgerGateway(db_path=tmp_path / "g.db", limits_path=limits_path)
    assert g2.effective_daily_budget(cfg) == 50


# ── Dashboard cost-limit endpoints (ASGITransport, tmp paths) ────────────────


async def test_limits_get_post_round_trip(tmp_path, monkeypatch):
    from lhassa.api import server
    from lhassa.llm.gateway import gateway

    # Redirect the singleton gateway to tmp paths and a clean limit state.
    monkeypatch.setattr(gateway, "db_path", tmp_path / "gateway.db")
    monkeypatch.setattr(gateway, "limits_path", tmp_path / "llm_limits.json")
    monkeypatch.setattr(gateway, "_initialized", False)
    monkeypatch.setattr(gateway, "_budget_override", None)
    monkeypatch.setattr(gateway, "_kill_switch", False)

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

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/llm/ledger")
        assert r.status_code == 200
        assert r.json()["kill_switch"] is True
