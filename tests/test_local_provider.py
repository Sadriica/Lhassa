"""Tests for the LOCAL heartbeat provider, the pseudonymizing wrapper, and the
router wiring that prefers local for routine thinking (§3-Fase 6).

Offline: ``LocalModelProvider`` is exercised against an httpx ``MockTransport``
(no real server); the router is exercised with a minimal config double.
"""

import httpx
import pytest

from lhassa.llm import router
from lhassa.llm.provider import (
    Completion,
    LocalModelProvider,
    PseudonymizingProvider,
    ProviderNotConfigured,
    get_model_provider,
)


# ── Config double ─────────────────────────────────────────────────────────────


class FakeConfig:
    def __init__(
        self,
        *,
        llm_provider="groq",
        model="cheap-model",
        model_cheap="cheap-model",
        model_smart="smart-model",
        router_mode="rules",
        local_model_url="",
        local_model="local-llama",
        heartbeat_provider="auto",
        pseudonymize_remote=False,
    ):
        self.llm_provider = llm_provider
        self.model = model
        self.model_cheap = model_cheap
        self.model_smart = model_smart
        self.router_mode = router_mode
        self.local_model_url = local_model_url
        self.local_model = local_model
        self.heartbeat_provider = heartbeat_provider
        self.pseudonymize_remote = pseudonymize_remote


def _factory(handler):
    """Return a client_factory that builds an AsyncClient over a MockTransport."""
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)


# ── LocalModelProvider ────────────────────────────────────────────────────────


async def test_local_provider_success_cost_zero():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "local-llama",
                "choices": [{"message": {"content": "hello back"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    cfg = FakeConfig(local_model_url="http://127.0.0.1:11434/v1")
    prov = LocalModelProvider(cfg, client_factory=_factory(handler))
    assert prov.name == "local"
    assert prov.zero_retention is True
    assert prov.is_local is True

    result = await prov.complete(
        [{"role": "user", "content": "hi"}], system="be brief", temperature=0.2
    )
    assert isinstance(result, Completion)
    assert result.text == "hello back"
    assert result.model == "local-llama"
    assert result.input_tokens == 12
    assert result.output_tokens == 7
    assert result.cost_usd == 0.0

    # Endpoint + system prepend + temperature were honored.
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["messages"][0] == {"role": "system", "content": "be brief"}
    assert captured["body"]["temperature"] == 0.2
    assert captured["body"]["model"] == "local-llama"


async def test_local_provider_no_usage_defaults_zero():
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    cfg = FakeConfig(local_model_url="http://127.0.0.1:11434/v1")
    prov = LocalModelProvider(cfg, client_factory=_factory(handler))
    result = await prov.complete([{"role": "user", "content": "hi"}])
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.cost_usd == 0.0
    assert result.model == "local-llama"  # falls back to config.local_model


async def test_local_provider_unreachable_raises():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    cfg = FakeConfig(local_model_url="http://127.0.0.1:11434/v1")
    prov = LocalModelProvider(cfg, client_factory=_factory(handler))
    with pytest.raises(ProviderNotConfigured, match="unreachable"):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_local_provider_non_200_raises():
    def handler(request):
        return httpx.Response(500, text="boom")

    cfg = FakeConfig(local_model_url="http://127.0.0.1:11434/v1")
    prov = LocalModelProvider(cfg, client_factory=_factory(handler))
    with pytest.raises(ProviderNotConfigured, match="HTTP 500"):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_local_provider_empty_url_raises():
    prov = LocalModelProvider(FakeConfig(local_model_url=""))
    with pytest.raises(ProviderNotConfigured):
        await prov.complete([{"role": "user", "content": "hi"}])


def test_get_model_provider_local():
    prov = get_model_provider(FakeConfig(), name="local")
    assert isinstance(prov, LocalModelProvider)
    assert prov.is_local is True


# ── PseudonymizingProvider ────────────────────────────────────────────────────


class RecordingProvider:
    """Records what it received; echoes a placeholder back to test de-scrubbing."""

    name = "default"
    zero_retention = False
    is_local = False

    def __init__(self):
        self.messages = None
        self.system = None

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        self.messages = messages
        self.system = opts.get("system")
        # Reply references the scrubbed email placeholder.
        return Completion(text="sent to <email_1>", model=model or "m")


async def test_pseudonymizing_provider_scrubs_and_restores():
    inner = RecordingProvider()
    wrap = PseudonymizingProvider(inner)
    assert wrap.name == "default"
    assert wrap.zero_retention is False
    assert wrap.is_local is False

    result = await wrap.complete(
        [{"role": "user", "content": "mail joe@corp.com now"}],
        system="owner is joe@corp.com",
    )
    # Inner provider never saw the raw email.
    assert "joe@corp.com" not in inner.messages[0]["content"]
    assert "joe@corp.com" not in inner.system
    # The shared mapping de-scrubs the reply back to the real value.
    assert result.text == "sent to joe@corp.com"


# ── Router wiring ─────────────────────────────────────────────────────────────


def test_pick_provider_routine_prefers_local():
    cfg = FakeConfig(local_model_url="http://127.0.0.1:11434/v1")
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert prov.is_local is True
    assert prov.name == "local"
    # Local is never wrapped in the pseudonymizer.
    assert not isinstance(prov, PseudonymizingProvider)


def test_pick_provider_hard_stays_cloud():
    cfg = FakeConfig(local_model_url="http://127.0.0.1:11434/v1")
    prov = router.pick_provider(sensitive=False, config=cfg, hard=True)
    assert getattr(prov, "is_local", False) is False
    assert prov.name == "default"


def test_pick_provider_heartbeat_cloud_never_local():
    cfg = FakeConfig(
        local_model_url="http://127.0.0.1:11434/v1", heartbeat_provider="cloud"
    )
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert getattr(prov, "is_local", False) is False


def test_pick_provider_cloud_wrapped_when_pseudonymize():
    cfg = FakeConfig(pseudonymize_remote=True)  # no local url → cloud
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert isinstance(prov, PseudonymizingProvider)
    assert prov.is_local is False


def test_pick_provider_local_not_wrapped_even_with_pseudonymize():
    cfg = FakeConfig(
        local_model_url="http://127.0.0.1:11434/v1", pseudonymize_remote=True
    )
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert prov.is_local is True
    assert not isinstance(prov, PseudonymizingProvider)


def test_pick_provider_heartbeat_local_unconfigured_falls_back(caplog):
    cfg = FakeConfig(local_model_url="", heartbeat_provider="local")
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert getattr(prov, "is_local", False) is False


def test_pick_model_local_uses_served_name():
    cfg = FakeConfig(local_model_url="http://127.0.0.1:11434/v1", local_model="llama3")
    local = get_model_provider(cfg, name="local")
    assert router.pick_model(local, hard=False, config=cfg) == "llama3"


def test_decide_routine_routes_local():
    cfg = FakeConfig(local_model_url="http://127.0.0.1:11434/v1", local_model="llama3")

    class T:
        kind = "task"
        params: dict = {}

    d = router.decide(T(), None, config=cfg)
    assert d.kind == "think"
    assert d.provider.is_local is True
    assert d.model == "llama3"
