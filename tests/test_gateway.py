"""Tests for lhassa.llm.gateway — cache, daily budget, and cost ledger.

All tests run fully offline: a fake completion callable stands in for the
provider, and the gateway DB is redirected to a tmp_path file so the real
home directory is never touched.
"""

import pytest
import pytest_asyncio  # noqa: F401  (registers asyncio mode)

from lhassa.llm.gateway import (
    BudgetExceeded,
    LedgerGateway,
    ToolTurnResult,
    estimate_cost,
    price_for,
)
from lhassa.state import state


# ── Test doubles ────────────────────────────────────────────────────────────


class FakeConfig:
    """Minimal stand-in for lhassa.config.Config — only the fields the gateway reads."""

    def __init__(
        self,
        *,
        llm_provider="anthropic",
        model="claude-opus-4-8-20251101",
        llm_cache_enabled=True,
        llm_daily_token_budget=0,
    ):
        self.llm_provider = llm_provider
        self.model = model
        self.llm_cache_enabled = llm_cache_enabled
        self.llm_daily_token_budget = llm_daily_token_budget


def make_fake_completion(text="hello", input_tokens=100, output_tokens=50):
    """Build a fake completion callable plus a call counter to assert on."""
    calls = {"n": 0}

    async def fake(provider, model, messages, max_tokens, temperature, system):
        calls["n"] += 1
        return text, input_tokens, output_tokens

    return fake, calls


def make_fake_tools_completion(
    text="done", tool_calls=None, input_tokens=100, output_tokens=50, stop_reason="end_turn"
):
    """Build a fake tools-completion callable plus a call counter to assert on.

    Matches the ToolCompletionFn signature the gateway invokes:
    ``(provider, model, messages, tools, system, max_tokens)``.
    """
    calls = {"n": 0}

    async def fake(provider, model, messages, tools, system, max_tokens):
        calls["n"] += 1
        return text, list(tool_calls or []), input_tokens, output_tokens, stop_reason

    return fake, calls


# A representative tool schema (shape is opaque to the gateway — it just hashes it).
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


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def gw(tmp_path):
    """A gateway whose DB lives in a throwaway tmp file (never ~/.lhassa)."""
    return LedgerGateway(db_path=tmp_path / "gateway.db")


@pytest.fixture
def metrics_guard():
    """Snapshot state.metrics before the test and restore it afterwards."""
    saved = dict(state.metrics)
    yield
    state.metrics.clear()
    state.metrics.update(saved)


MESSAGES = [{"role": "user", "content": "Hi there"}]


# ── Pricing / cost ──────────────────────────────────────────────────────────


def test_price_for_known_anthropic_model():
    assert price_for("claude-opus-4-8-20251101") == (5.0, 25.0)
    assert price_for("claude-sonnet-4-6") == (3.0, 15.0)
    assert price_for("claude-haiku-4-5-20251001") == (1.0, 5.0)
    assert price_for("claude-fable-5") == (10.0, 50.0)


def test_price_for_unknown_model_is_zero():
    assert price_for("llama-3.3-70b-versatile") == (0.0, 0.0)
    assert price_for("gpt-4o-mini") == (0.0, 0.0)
    assert price_for("") == (0.0, 0.0)


def test_estimate_cost_known_model():
    # 1M input @ $5 + 1M output @ $25 = $30
    assert estimate_cost("claude-opus-4-8", 1_000_000, 1_000_000) == pytest.approx(30.0)
    # 100 in / 50 out on opus-4-8 → 100*5/1e6 + 50*25/1e6
    expected = (100 * 5.0 + 50 * 25.0) / 1_000_000
    assert estimate_cost("claude-opus-4-8", 100, 50) == pytest.approx(expected)


def test_estimate_cost_unknown_model_is_zero():
    assert estimate_cost("ollama/llama3.2", 1000, 1000) == 0.0


# ── Cache ───────────────────────────────────────────────────────────────────


async def test_cache_miss_then_hit(gw, metrics_guard):
    cfg = FakeConfig()
    fake, calls = make_fake_completion(text="cached answer")

    before_hits = state.metrics["llm_cache_hits"]

    first = await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert first.text == "cached answer"
    assert first.cache_hit is False
    assert calls["n"] == 1

    second = await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert second.text == "cached answer"
    assert second.cache_hit is True
    # Model was NOT called again on the hit.
    assert calls["n"] == 1
    assert state.metrics["llm_cache_hits"] == before_hits + 1


async def test_cache_disabled_always_calls_model(gw, metrics_guard):
    cfg = FakeConfig(llm_cache_enabled=False)
    fake, calls = make_fake_completion()

    await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert calls["n"] == 2


async def test_cache_key_varies_with_params(gw, metrics_guard):
    cfg = FakeConfig()
    fake, calls = make_fake_completion()

    await gw.complete(MESSAGES, config=cfg, max_tokens=100, completion_fn=fake)
    # Different max_tokens → different key → live call, not a hit.
    await gw.complete(MESSAGES, config=cfg, max_tokens=200, completion_fn=fake)
    assert calls["n"] == 2


# ── Budget ──────────────────────────────────────────────────────────────────


async def test_budget_unlimited_allows_calls(gw, metrics_guard):
    cfg = FakeConfig(llm_daily_token_budget=0, llm_cache_enabled=False)
    fake, calls = make_fake_completion(output_tokens=1000)

    for _ in range(5):
        await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert calls["n"] == 5


async def test_budget_enforced_at_limit(gw, metrics_guard):
    # Budget of 50 output tokens; each call spends 50.
    cfg = FakeConfig(llm_daily_token_budget=50, llm_cache_enabled=False)
    fake, _ = make_fake_completion(output_tokens=50)

    # First call: spent (0) < budget (50) → allowed, brings spend to 50.
    await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert await gw.daily_output_tokens() == 50

    # Second call: spent (50) >= budget (50) → refused.
    with pytest.raises(BudgetExceeded):
        await gw.complete(MESSAGES, config=cfg, completion_fn=fake)


async def test_budget_does_not_block_cache_hits(gw, metrics_guard):
    cfg = FakeConfig(llm_daily_token_budget=50)
    fake, calls = make_fake_completion(output_tokens=50)

    # Prime the cache (spends 50, hits the limit).
    await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    # Identical prompt is served from cache even though budget is exhausted.
    result = await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert result.cache_hit is True
    assert calls["n"] == 1


# ── Ledger / metrics ────────────────────────────────────────────────────────


async def test_ledger_updates_state_metrics(gw, metrics_guard):
    cfg = FakeConfig(llm_cache_enabled=False)
    fake, _ = make_fake_completion(input_tokens=100, output_tokens=50)

    base_calls = state.metrics["llm_calls"]
    base_in = state.metrics["llm_input_tokens"]
    base_out = state.metrics["llm_output_tokens"]
    base_cost = state.metrics["llm_cost_usd"]

    result = await gw.complete(MESSAGES, config=cfg, completion_fn=fake)

    expected_cost = (100 * 5.0 + 50 * 25.0) / 1_000_000
    assert result.cost_usd == pytest.approx(expected_cost)
    assert state.metrics["llm_calls"] == base_calls + 1
    assert state.metrics["llm_input_tokens"] == base_in + 100
    assert state.metrics["llm_output_tokens"] == base_out + 50
    assert state.metrics["llm_cost_usd"] == pytest.approx(base_cost + expected_cost)


async def test_ledger_persists_rows(gw, metrics_guard):
    cfg = FakeConfig()
    fake, _ = make_fake_completion(input_tokens=10, output_tokens=20)

    await gw.complete(MESSAGES, config=cfg, completion_fn=fake)  # live
    await gw.complete(MESSAGES, config=cfg, completion_fn=fake)  # cache hit

    rows = await gw.ledger_rows()
    assert len(rows) == 2
    assert rows[0]["cache_hit"] == 0
    assert rows[0]["input_tokens"] == 10
    assert rows[0]["output_tokens"] == 20
    assert rows[1]["cache_hit"] == 1
    assert rows[1]["cost_usd"] == 0.0


async def test_unknown_model_costs_zero_in_ledger(gw, metrics_guard):
    cfg = FakeConfig(llm_provider="groq", model="llama-3.3-70b-versatile",
                     llm_cache_enabled=False)
    fake, _ = make_fake_completion(input_tokens=500, output_tokens=500)

    result = await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert result.provider == "openai"  # groq speaks the OpenAI protocol
    assert result.cost_usd == 0.0

    rows = await gw.ledger_rows()
    assert rows[-1]["cost_usd"] == 0.0


async def test_openai_provider_resolution(gw, metrics_guard):
    cfg = FakeConfig(llm_provider="openai", model="gpt-4o-mini",
                     llm_cache_enabled=False)
    fake, _ = make_fake_completion()
    result = await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert result.provider == "openai"


async def test_anthropic_provider_resolution(gw, metrics_guard):
    cfg = FakeConfig(llm_provider="anthropic", model="claude-opus-4-8")
    fake, _ = make_fake_completion()
    result = await gw.complete(MESSAGES, config=cfg, completion_fn=fake)
    assert result.provider == "anthropic"


# ── Tool-aware turns (complete_with_tools) ───────────────────────────────────


async def test_tool_turn_no_tools_updates_ledger_and_caches(gw, metrics_guard):
    """A no-tool (deterministic) turn updates the ledger AND is cached."""
    cfg = FakeConfig()
    fake, calls = make_fake_tools_completion(text="final answer", tool_calls=[])

    base_calls = state.metrics["llm_calls"]
    before_hits = state.metrics["llm_cache_hits"]

    first = await gw.complete_with_tools(MESSAGES, TOOLS, config=cfg, completion_fn=fake)
    assert isinstance(first, ToolTurnResult)
    assert first.text == "final answer"
    assert first.tool_calls == []
    assert first.cache_hit is False
    assert calls["n"] == 1
    assert state.metrics["llm_calls"] == base_calls + 1

    # Identical call → served from cache; the provider fn is NOT invoked again.
    second = await gw.complete_with_tools(MESSAGES, TOOLS, config=cfg, completion_fn=fake)
    assert second.cache_hit is True
    assert second.text == "final answer"
    assert second.stop_reason == "end_turn"
    assert calls["n"] == 1
    assert state.metrics["llm_cache_hits"] == before_hits + 1


async def test_tool_turn_with_tools_updates_ledger_but_not_cached(gw, metrics_guard):
    """A turn that returns tool calls updates the ledger but is NEVER cached."""
    cfg = FakeConfig()
    tool_calls = [{"id": "t1", "name": "web_search", "input": {"q": "weather"}}]
    fake, calls = make_fake_tools_completion(
        text="", tool_calls=tool_calls, stop_reason="tool_use"
    )

    base_calls = state.metrics["llm_calls"]

    first = await gw.complete_with_tools(MESSAGES, TOOLS, config=cfg, completion_fn=fake)
    assert first.tool_calls == tool_calls
    assert first.cache_hit is False
    assert state.metrics["llm_calls"] == base_calls + 1

    # Identical call again → still a live call (no caching of tool turns).
    second = await gw.complete_with_tools(MESSAGES, TOOLS, config=cfg, completion_fn=fake)
    assert second.cache_hit is False
    assert calls["n"] == 2
    assert state.metrics["llm_calls"] == base_calls + 2


async def test_tool_turn_budget_enforced(gw, metrics_guard):
    cfg = FakeConfig(llm_daily_token_budget=50, llm_cache_enabled=False)
    fake, _ = make_fake_tools_completion(output_tokens=50, tool_calls=[])

    # First call spends 50 → reaches the limit.
    await gw.complete_with_tools(MESSAGES, TOOLS, config=cfg, completion_fn=fake)
    assert await gw.daily_output_tokens() == 50

    # Second call: spent (50) >= budget (50) → refused.
    with pytest.raises(BudgetExceeded):
        await gw.complete_with_tools(MESSAGES, TOOLS, config=cfg, completion_fn=fake)


async def test_tool_turn_cost_correct_for_known_model(gw, metrics_guard):
    cfg = FakeConfig(model="claude-opus-4-8-20251101", llm_cache_enabled=False)
    fake, _ = make_fake_tools_completion(input_tokens=100, output_tokens=50, tool_calls=[])

    result = await gw.complete_with_tools(MESSAGES, TOOLS, config=cfg, completion_fn=fake)
    expected = (100 * 5.0 + 50 * 25.0) / 1_000_000
    assert result.cost_usd == pytest.approx(expected)
    assert result.input_tokens == 100
    assert result.output_tokens == 50

    rows = await gw.ledger_rows()
    assert rows[-1]["cache_hit"] == 0
    assert rows[-1]["cost_usd"] == pytest.approx(expected)
