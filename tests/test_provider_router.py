"""Tests for the ModelProvider seam (axis 1) and the router (two axes + cascade).

Fully offline: the GatewayProvider runs through a tmp-file LedgerGateway with an
injected completion callable; the router is exercised with fake providers and a
minimal config double. No network, no live LLM.
"""

import asyncio
import json

import pytest
import pytest_asyncio  # noqa: F401  (registers asyncio mode)

from lhassa.llm import router
from lhassa.llm.gateway import LedgerGateway
from lhassa.llm.provider import (
    ClaudeCodeProvider,
    CodexProvider,
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


def test_get_model_provider_unknown_name_falls_back_to_default():
    # Historical behaviour: an unknown name degrades to the GatewayProvider.
    assert isinstance(get_model_provider(FakeConfig(), name="bogus"), GatewayProvider)


def test_get_model_provider_resolves_cli_subscriptions():
    assert isinstance(get_model_provider(FakeConfig(), name="claude"), ClaudeCodeProvider)
    assert isinstance(get_model_provider(FakeConfig(), name="codex"), CodexProvider)


# ── CLI-subscription providers (Claude Code / Codex); runner seam mocked ─────
#
# These mock the injected subprocess ``runner`` so no real CLI is ever spawned.


def _runner(returncode=0, stdout=b"", stderr=b""):
    """An injectable ``runner`` returning a fixed (returncode, stdout, stderr)."""

    async def run(argv):
        return returncode, stdout, stderr

    return run


def _capturing_runner(captured, *, returncode=0, stdout=b"", stderr=b""):
    """A runner that records argv into ``captured`` before returning."""

    async def run(argv):
        captured["argv"] = argv
        return returncode, stdout, stderr

    return run


async def test_claude_code_provider_parses_json():
    out = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "hello there",
            "total_cost_usd": 0.0123,
            "usage": {"input_tokens": 12, "output_tokens": 5},
        }
    ).encode()
    prov = ClaudeCodeProvider(FakeConfig(), runner=_runner(0, out))
    assert prov.name == "claude"
    assert isinstance(prov, ModelProvider)
    c = await prov.complete([{"role": "user", "content": "hi"}])
    assert c.text == "hello there"
    # The CLI's total_cost_usd is an API-equivalent ESTIMATE; the subscription's
    # marginal cash cost is $0 BY DESIGN; it must never enter the ledger.
    assert c.cost_usd == 0.0
    assert c.input_tokens == 12 and c.output_tokens == 5


async def test_claude_code_provider_model_and_system_in_argv():
    captured = {}
    out = json.dumps({"type": "result", "result": "ok"}).encode()
    prov = ClaudeCodeProvider(FakeConfig(), runner=_capturing_runner(captured, stdout=out))
    await prov.complete(
        [{"role": "user", "content": "hi"}], model="opus", system="be brief"
    )
    argv = captured["argv"]
    assert argv[0] == "claude"
    assert "--print" in argv and "--output-format" in argv and "json" in argv
    assert "--model" in argv and "opus" in argv
    assert "be brief" in argv[-1]  # system prepended into the flattened prompt


async def test_claude_code_provider_nonzero_exit_raises():
    prov = ClaudeCodeProvider(FakeConfig(), runner=_runner(1, b"", b"boom"))
    with pytest.raises(ProviderNotConfigured):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_claude_code_provider_missing_binary_raises():
    async def run(argv):
        raise FileNotFoundError("no claude")

    prov = ClaudeCodeProvider(FakeConfig(), runner=run)
    with pytest.raises(ProviderNotConfigured):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_claude_code_provider_timeout_raises():
    async def run(argv):
        raise asyncio.TimeoutError()

    prov = ClaudeCodeProvider(FakeConfig(), runner=run)
    with pytest.raises(ProviderNotConfigured):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_claude_code_provider_empty_output_raises():
    out = json.dumps({"type": "result", "result": ""}).encode()
    prov = ClaudeCodeProvider(FakeConfig(), runner=_runner(0, out))
    with pytest.raises(ProviderNotConfigured):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_codex_provider_parses_jsonl():
    lines = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {"type": "item.completed",
         "item": {"id": "item_0", "type": "agent_message", "text": "pong"}},
        {"type": "turn.completed", "usage": {"input_tokens": 40, "output_tokens": 17}},
    ]
    out = ("\n".join(json.dumps(line) for line in lines)).encode()
    prov = CodexProvider(FakeConfig(), runner=_runner(0, out))
    assert prov.name == "codex"
    assert isinstance(prov, ModelProvider)
    c = await prov.complete([{"role": "user", "content": "hi"}])
    assert c.text == "pong"
    assert c.cost_usd == 0.0  # codex reports no cost
    assert c.input_tokens == 40 and c.output_tokens == 17


async def test_codex_provider_argv_has_exec_and_model():
    captured = {}
    out = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}
    ).encode()
    prov = CodexProvider(FakeConfig(), runner=_capturing_runner(captured, stdout=out))
    await prov.complete([{"role": "user", "content": "hi"}], model="gpt-x")
    argv = captured["argv"]
    assert argv[:2] == ["codex", "exec"]
    assert "--json" in argv and "-m" in argv and "gpt-x" in argv
    assert argv[-1].endswith("hi")  # prompt is the trailing positional arg


async def test_codex_provider_nonzero_exit_raises():
    prov = CodexProvider(FakeConfig(), runner=_runner(1, b"", b"boom"))
    with pytest.raises(ProviderNotConfigured):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_codex_provider_missing_binary_raises():
    async def run(argv):
        raise FileNotFoundError("no codex")

    prov = CodexProvider(FakeConfig(), runner=run)
    with pytest.raises(ProviderNotConfigured):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_codex_provider_timeout_raises():
    async def run(argv):
        raise asyncio.TimeoutError()

    prov = CodexProvider(FakeConfig(), runner=run)
    with pytest.raises(ProviderNotConfigured):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_codex_provider_empty_output_raises():
    # JSON parsed but no agent_message event → treated as empty → not configured.
    out = json.dumps({"type": "turn.completed", "usage": {}}).encode()
    prov = CodexProvider(FakeConfig(), runner=_runner(0, out))
    with pytest.raises(ProviderNotConfigured):
        await prov.complete([{"role": "user", "content": "hi"}])


# ── Attribution (Defect 1): CLI providers report their ACTUAL model ───────────


async def test_claude_provider_reports_actual_model_from_json():
    """The Completion carries the model the CLI actually ran (parsed from JSON)."""
    out = json.dumps(
        {"type": "result", "result": "hi", "model": "claude-sonnet-4-6"}
    ).encode()
    prov = ClaudeCodeProvider(FakeConfig(), runner=_runner(0, out))
    c = await prov.complete([{"role": "user", "content": "hi"}])
    assert c.model == "claude-sonnet-4-6"


async def test_claude_provider_reports_cli_marker_when_model_absent():
    out = json.dumps({"type": "result", "result": "hi"}).encode()
    prov = ClaudeCodeProvider(FakeConfig(), runner=_runner(0, out))
    c = await prov.complete([{"role": "user", "content": "hi"}])
    # No API model name is invented; a truthful "claude-cli" marker instead.
    assert c.model == "claude-cli"


async def test_claude_provider_drops_cli_marker_and_never_forwards_it():
    captured = {}
    out = json.dumps({"type": "result", "result": "ok"}).encode()
    prov = ClaudeCodeProvider(FakeConfig(), runner=_capturing_runner(captured, stdout=out))
    # The router marker must NOT reach --model (it would 404) → CLI picks its own.
    await prov.complete([{"role": "user", "content": "hi"}], model="claude-cli")
    assert "--model" not in captured["argv"]


async def test_codex_provider_reports_actual_model_from_json():
    lines = [
        {"type": "session.created", "model": "gpt-5-codex"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "pong"}},
    ]
    out = ("\n".join(json.dumps(line) for line in lines)).encode()
    prov = CodexProvider(FakeConfig(), runner=_runner(0, out))
    c = await prov.complete([{"role": "user", "content": "hi"}])
    assert c.model == "gpt-5-codex"


async def test_codex_provider_drops_cli_marker_and_never_forwards_it():
    captured = {}
    out = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}
    ).encode()
    prov = CodexProvider(FakeConfig(), runner=_capturing_runner(captured, stdout=out))
    await prov.complete([{"role": "user", "content": "hi"}], model="codex-cli")
    assert "-m" not in captured["argv"]


def test_pick_model_cli_provider_never_claims_api_model():
    # The bug: a claude/codex CLI provider paired with the config's groq model.
    cfg = FakeConfig(model_cheap="llama-3.3-70b-versatile",
                     model_smart="llama-3.3-70b-versatile")
    assert router.pick_model(FakeProvider(name="claude"), hard=False, config=cfg) == "claude-cli"
    assert router.pick_model(FakeProvider(name="codex"), hard=True, config=cfg) == "codex-cli"


def test_pick_model_cli_provider_honors_configured_model():
    cfg = FakeConfig()
    cfg.claude_model = "claude-opus-4-8"
    cfg.codex_model = "gpt-5-codex"
    assert router.pick_model(FakeProvider(name="claude"), hard=False, config=cfg) == "claude-opus-4-8"
    assert router.pick_model(FakeProvider(name="codex"), hard=False, config=cfg) == "gpt-5-codex"


def test_decide_claude_reason_is_truthful(monkeypatch):
    """decide() routed to the claude CLI must not claim the config's groq model."""
    def fake_gmp(config, name=None):
        return FakeProvider(name=name or "default")

    monkeypatch.setattr(router, "get_model_provider", fake_gmp)
    cfg = FakeConfig(model_cheap="llama-3.3-70b-versatile")
    cfg.heartbeat_provider = "claude"
    d = router.decide(FakeTrigger(), None, config=cfg)
    assert d.provider.name == "claude"
    assert "model=claude-cli" in d.reason
    assert "llama-3.3-70b-versatile" not in d.reason  # no attribution lie


# ── keystone registry: introspection + drop-in seam ──────────────────────────


def test_providers_registry_exposes_builtin_names():
    from lhassa.llm import provider as P
    from lhassa.registry import Registry

    assert isinstance(P.PROVIDERS, Registry)
    assert set(P.PROVIDERS.names()) == {
        "default", "local", "opencode", "claude", "codex", "zen"
    }


def test_register_provider_dropin_and_resolves():
    """A plugin can register a new provider factory, then get_model_provider resolves it."""
    from lhassa.llm import provider as P

    sentinel = FakeProvider(name="plugin")

    try:
        P.register_provider("plugin", lambda config: sentinel)
        got = get_model_provider(FakeConfig(), name="plugin")
        assert got is sentinel
        assert "plugin" in P.PROVIDERS.names()
    finally:
        P.PROVIDERS.unregister("plugin")


def test_register_provider_override_protection():
    from lhassa.llm import provider as P

    with pytest.raises(ValueError, match="already registered"):
        P.register_provider("default", lambda config: None)
    # override=True replaces without raising; restore the original factory after.
    original = P.PROVIDERS.get("default")
    try:
        P.register_provider("default", lambda config: FakeProvider(name="x"), override=True)
        assert get_model_provider(FakeConfig()).name == "x"
    finally:
        P.register_provider("default", original, override=True)
        assert isinstance(get_model_provider(FakeConfig()), GatewayProvider)


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


# ── Router: heartbeat_provider → claude / codex CLI subscriptions ─────────────


def _route_with_heartbeat(monkeypatch, heartbeat, *, hard=False):
    """Drive pick_provider with a chosen heartbeat_provider, mocking get_model_provider."""
    captured = {}

    def fake_gmp(config, name=None):
        captured["name"] = name
        return FakeProvider(name=name or "default")

    monkeypatch.setattr(router, "get_model_provider", fake_gmp)
    cfg = FakeConfig()
    cfg.heartbeat_provider = heartbeat
    prov = router.pick_provider(sensitive=False, config=cfg, hard=hard)
    return prov, captured


def test_pick_provider_heartbeat_claude_routes_to_claude(monkeypatch):
    prov, captured = _route_with_heartbeat(monkeypatch, "claude")
    assert captured["name"] == "claude"
    assert prov.name == "claude"


def test_pick_provider_heartbeat_codex_routes_to_codex(monkeypatch):
    prov, captured = _route_with_heartbeat(monkeypatch, "codex")
    assert captured["name"] == "codex"
    assert prov.name == "codex"


def test_pick_provider_heartbeat_claude_hard_path_ignores_cli(monkeypatch):
    # The HARD path is unchanged: it goes to the cloud default, not the claude CLI.
    prov, captured = _route_with_heartbeat(monkeypatch, "claude", hard=True)
    assert captured["name"] is None  # default gateway, no CLI name forced
    assert prov.name == "default"


def test_decide_routine_routes_to_codex_when_configured(monkeypatch):
    captured = {}

    def fake_gmp(config, name=None):
        captured["name"] = name
        return FakeProvider(name=name or "default")

    monkeypatch.setattr(router, "get_model_provider", fake_gmp)
    cfg = FakeConfig()
    cfg.heartbeat_provider = "codex"
    d = router.decide(FakeTrigger(), None, config=cfg)
    assert d.kind == "think" and d.provider.name == "codex"
    assert captured["name"] == "codex"


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


# ── Router: budget soft-limit reads the persisted ledger, not the in-process
#    mirror (the do-and-sleep execution model means a fresh heartbeat process
#    starts with an empty ``state.metrics`` mirror every single beat) ─────────


@pytest.fixture
def budget_cache_guard():
    """``_budget_pct_used`` caches per UTC day in a module dict; reset it so
    one test's cached read never leaks into the next."""
    router._budget_cache.clear()
    yield
    router._budget_cache.clear()


async def test_budget_pct_used_reads_persisted_ledger_new_process(
    tmp_path, monkeypatch, metrics_guard, budget_cache_guard,
):
    """A brand-new process (empty in-memory mirror) must still see spend that
    was persisted to the gateway ledger by a *previous* process; this is
    exactly the do-and-sleep gap: each heartbeat beat is a new ``systemd``
    process, so the in-memory mirror alone can never trip the soft brake.
    """
    from lhassa.llm import gateway as gw

    test_gateway = LedgerGateway(db_path=tmp_path / "ledger.db")
    await test_gateway._ensure_db()
    day = gw._today()
    await test_gateway._add_daily_spend(day, 900)  # 90% of a 1000-token budget

    # Stand in for "this is a fresh process": no in-process spend recorded.
    state.metrics.pop("llm_output_tokens", None)
    monkeypatch.setattr(gw, "gateway", test_gateway)

    cfg = FakeConfig(llm_daily_token_budget=1000)
    pct = router._budget_pct_used(cfg)
    assert pct == pytest.approx(0.9)


async def test_decide_downgrades_hard_when_persisted_spend_crosses_soft_pct(
    tmp_path, monkeypatch, metrics_guard, budget_cache_guard,
):
    """The soft brake (default 80%) must fire off the persisted ledger alone,
    with no help from ``state.metrics``: reproducing "new process, ledger
    already past soft pct" end to end through ``decide``.
    """
    from lhassa.llm import gateway as gw

    test_gateway = LedgerGateway(db_path=tmp_path / "ledger.db")
    await test_gateway._ensure_db()
    await test_gateway._add_daily_spend(gw._today(), 850)  # 85% of 1000

    state.metrics.pop("llm_output_tokens", None)
    monkeypatch.setattr(gw, "gateway", test_gateway)

    cfg = FakeConfig(llm_daily_token_budget=1000)
    d = router.decide(FakeTrigger(params={"hard": True}), None, config=cfg)
    assert d.model == "cheap-model"
    assert "budget_downgrade" in d.reason


async def test_budget_pct_used_falls_back_to_mirror_on_ledger_error(
    monkeypatch, metrics_guard, budget_cache_guard,
):
    """A broken ledger read degrades to the previous (in-process mirror)
    behaviour rather than raising or silently disabling the brake."""
    from lhassa.llm import gateway as gw

    class BrokenGateway:
        def daily_output_tokens_sync(self, day=None):
            raise RuntimeError("db is locked")

    monkeypatch.setattr(gw, "gateway", BrokenGateway())
    state.metrics["llm_output_tokens"] = 950

    cfg = FakeConfig(llm_daily_token_budget=1000)
    pct = router._budget_pct_used(cfg)
    assert pct == pytest.approx(0.95)


def test_budget_pct_used_unlimited_returns_zero(budget_cache_guard):
    cfg = FakeConfig(llm_daily_token_budget=0)
    assert router._budget_pct_used(cfg) == 0.0


def test_daily_output_tokens_sync_missing_db_returns_zero(tmp_path):
    gw = LedgerGateway(db_path=tmp_path / "nope.db")
    assert gw.daily_output_tokens_sync() == 0


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
