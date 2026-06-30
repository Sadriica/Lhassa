"""Tests for the ModelProvider seam (axis 1) and the router (two axes + cascade).

Fully offline: the GatewayProvider runs through a tmp-file LedgerGateway with an
injected completion callable; the router is exercised with fake providers and a
minimal config double. No network, no live LLM.
"""

import pytest
import pytest_asyncio  # noqa: F401  (registers asyncio mode)

from lhassa.llm import router
from lhassa.llm.gateway import LedgerGateway
from lhassa.llm.provider import (
    Completion,
    GatewayProvider,
    ModelProvider,
    OpenCodeZenProvider,
    ProviderNotConfigured,
    get_model_provider,
)
from lhassa.state import state


# ── Config / provider doubles ────────────────────────────────────────────────


class FakeConfig:
    """Minimal Config stand-in covering what the gateway + router read."""

    def __init__(
        self,
        *,
        llm_provider="groq",
        model="cheap-model",
        model_cheap="cheap-model",
        model_smart="smart-model",
        router_mode="rules",
        llm_cache_enabled=True,
        llm_daily_token_budget=0,
    ):
        self.llm_provider = llm_provider
        self.model = model
        self.model_cheap = model_cheap
        self.model_smart = model_smart
        self.router_mode = router_mode
        self.llm_cache_enabled = llm_cache_enabled
        self.llm_daily_token_budget = llm_daily_token_budget


class FakeProvider:
    """Records the model of each call and returns a canned Completion per model."""

    def __init__(self, *, name="fake", zero_retention=False):
        self.name = name
        self.zero_retention = zero_retention
        self.models = []

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        self.models.append(model)
        return Completion(text=f"ans-{model}", model=model or "?", cost_usd=0.0)


class FakeTrigger:
    def __init__(self, *, action="decide", params=None):
        self.kind = "task"
        self.action = action
        self.objetivo = "x"
        self.params = params or {}


@pytest.fixture
def metrics_guard():
    saved = dict(state.metrics)
    yield
    state.metrics.clear()
    state.metrics.update(saved)


# ── ModelProvider implementations ─────────────────────────────────────────────


def test_get_model_provider_defaults_to_gateway():
    prov = get_model_provider(FakeConfig())
    assert isinstance(prov, GatewayProvider)
    assert prov.name == "default"
    assert isinstance(prov, ModelProvider)  # runtime_checkable Protocol


def test_gateway_provider_zero_retention_by_upstream():
    assert get_model_provider(FakeConfig(llm_provider="anthropic")).zero_retention is True
    assert get_model_provider(FakeConfig(llm_provider="openai")).zero_retention is True
    assert get_model_provider(FakeConfig(llm_provider="groq")).zero_retention is False


async def test_gateway_provider_complete_updates_ledger(tmp_path, metrics_guard):
    """GatewayProvider.complete routes through the gateway, costing + logging the call."""
    gw = LedgerGateway(db_path=tmp_path / "gw.db")
    cfg = FakeConfig(llm_provider="anthropic", model="claude-opus-4-8",
                     llm_cache_enabled=False)
    prov = GatewayProvider(cfg, gateway=gw)

    async def fake_completion(provider, model, messages, max_tokens, temperature, system):
        return "live answer", 100, 50

    result = await prov.complete(
        [{"role": "user", "content": "hi"}],
        model="claude-opus-4-8",
        completion_fn=fake_completion,
    )
    assert isinstance(result, Completion)
    assert result.text == "live answer"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    # opus-4-8: 100*5 + 50*25 per 1M
    assert result.cost_usd == pytest.approx((100 * 5.0 + 50 * 25.0) / 1_000_000)

    rows = await gw.ledger_rows()
    assert len(rows) == 1
    assert rows[-1]["output_tokens"] == 50


async def test_opencode_zen_provider_raises_until_configured():
    prov = OpenCodeZenProvider(FakeConfig())
    assert prov.name == "zen"
    assert prov.zero_retention is False
    with pytest.raises(ProviderNotConfigured):
        await prov.complete([{"role": "user", "content": "hi"}])


def test_get_model_provider_zen_stub():
    assert isinstance(get_model_provider(FakeConfig(), name="zen"), OpenCodeZenProvider)


# ── Router: pick_provider (sensitivity) ───────────────────────────────────────


def test_pick_provider_sensitive_prefers_zero_retention():
    prov = router.pick_provider(sensitive=True, config=FakeConfig(llm_provider="anthropic"))
    assert prov.zero_retention is True


def test_pick_provider_sensitive_warns_when_no_zero_retention(caplog):
    prov = router.pick_provider(sensitive=True, config=FakeConfig(llm_provider="groq"))
    # Falls back to the default (not zero-retention) rather than failing.
    assert prov.zero_retention is False


def test_pick_provider_not_sensitive_returns_default():
    prov = router.pick_provider(sensitive=False, config=FakeConfig(llm_provider="groq"))
    assert prov.name == "default"


# ── Router: pick_model (cheap vs smart) ───────────────────────────────────────


def test_pick_model_routine_is_cheap():
    cfg = FakeConfig()
    assert router.pick_model(FakeProvider(), hard=False, config=cfg) == "cheap-model"


def test_pick_model_hard_is_smart():
    cfg = FakeConfig()
    assert router.pick_model(FakeProvider(), hard=True, config=cfg) == "smart-model"


def test_pick_model_router_off_uses_base_model():
    cfg = FakeConfig(router_mode="off", model="base")
    assert router.pick_model(FakeProvider(), hard=True, config=cfg) == "base"


# ── Router: decide ────────────────────────────────────────────────────────────


def test_decide_routine_trigger_is_think_cheap():
    cfg = FakeConfig()
    d = router.decide(FakeTrigger(), None, config=cfg)
    assert d.kind == "think"
    assert d.model == "cheap-model"
    assert d.provider is not None
    assert "think:" in d.reason


def test_decide_hard_trigger_uses_smart():
    cfg = FakeConfig()
    d = router.decide(FakeTrigger(params={"hard": True}), None, config=cfg)
    assert d.model == "smart-model"


def test_decide_sensitive_routes_zero_retention():
    cfg = FakeConfig(llm_provider="anthropic")
    d = router.decide(FakeTrigger(params={"sensitive": True}), None, config=cfg)
    assert d.provider.zero_retention is True
    assert "sensitive=True" in d.reason


def test_decide_force_think_uses_smart():
    cfg = FakeConfig()
    d = router.decide(FakeTrigger(), None, config=cfg, force_think=True)
    assert d.model == "smart-model"
    assert "forced=think" in d.reason


def test_decide_force_justdo_uses_cheap():
    cfg = FakeConfig()
    d = router.decide(FakeTrigger(params={"hard": True}), None, config=cfg, force_justdo=True)
    # $justdo overrides the hard heuristic → cheap, no deliberation.
    assert d.model == "cheap-model"
    assert "forced=justdo" in d.reason


# ── Router: cascade ───────────────────────────────────────────────────────────


async def test_cascade_no_verify_returns_cheap():
    prov = FakeProvider()
    cfg = FakeConfig()
    result = await router.cascade([{"role": "user", "content": "q"}], config=cfg, provider=prov)
    assert result.model == "cheap-model"
    assert prov.models == ["cheap-model"]  # never escalated


async def test_cascade_verify_pass_returns_cheap():
    prov = FakeProvider()
    cfg = FakeConfig()
    result = await router.cascade(
        [{"role": "user", "content": "q"}], config=cfg, provider=prov,
        verify=lambda c: True,
    )
    assert result.model == "cheap-model"
    assert prov.models == ["cheap-model"]


async def test_cascade_verify_fail_escalates_once():
    prov = FakeProvider()
    cfg = FakeConfig()
    result = await router.cascade(
        [{"role": "user", "content": "q"}], config=cfg, provider=prov,
        verify=lambda c: False,
    )
    assert result.model == "smart-model"
    # Cheap first, then exactly one escalation to smart.
    assert prov.models == ["cheap-model", "smart-model"]
