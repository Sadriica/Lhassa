"""
LLM provider factory — selects the right Pipecat service and async client
based on LHASSA_LLM_PROVIDER.

Supported providers:
  groq         — Groq API (default, free tier)
  anthropic    — Anthropic Claude
  openai       — OpenAI
  openrouter   — OpenRouter (access many models via one key)
  ollama       — Local Ollama instance (no API key)
  cerebras     — Cerebras (fast inference)
"""

from __future__ import annotations

from dataclasses import dataclass
from loguru import logger
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from lhassa.config import Config

# Module-level cache — reuses connection pools across agent instances
_agent_clients: dict[tuple, Any] = {}


# ── Axis 1: ModelProvider — "think directly", no harness (§2.2) ───────────────
#
# The durable seam for the brain to reason: heartbeat, classification, decisions,
# memory consolidation. Phase 2 ships one live implementation (GatewayProvider,
# wrapping the existing key through the cost gateway) and one ready-to-enable
# stub (OpenCodeZenProvider, the future free/cheap heartbeat). A local provider
# (zero_retention=True) replaces the heartbeat later — only this seam changes.


class ProviderNotConfigured(RuntimeError):
    """Raised when a ModelProvider has no usable credentials/backend yet."""


@dataclass
class Completion:
    """One model answer plus the costing metadata the gateway logged."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@runtime_checkable
class ModelProvider(Protocol):
    """A backend that can *think directly* (no agentic harness).

    ``zero_retention`` records whether the backend trains on the data sent to it.
    The router (§2.4) routes sensitive data only to ``zero_retention`` providers.
    """

    name: str
    zero_retention: bool

    async def complete(
        self, messages: list[dict], *, model: str | None = None, max_tokens: int = 1024, **opts
    ) -> Completion: ...


# First-party APIs that are not trained on by default → treated as zero-retention.
# Anything else (free tiers, local proxies, unknown) is conservatively NOT.
_ZERO_RETENTION_PROVIDERS = {"anthropic", "openai"}

# Keys of ``complete(**opts)`` that the gateway understands; others are dropped so
# an unknown opt never reaches the provider client.
_GATEWAY_OPT_KEYS = ("temperature", "system", "completion_fn")


class GatewayProvider:
    """Active provider: routes ``complete()`` through the cost gateway.

    Cache, daily budget, and the cost ledger all apply because every call goes
    through ``gateway.complete(...)``. ``zero_retention`` is derived conservatively
    from the configured upstream provider.
    """

    name = "default"

    def __init__(self, config: "Config", *, gateway: Any | None = None):
        self._config = config
        if gateway is None:
            from lhassa.llm.gateway import gateway as default_gateway

            gateway = default_gateway
        self._gateway = gateway

    @property
    def zero_retention(self) -> bool:
        return getattr(self._config, "llm_provider", "") in _ZERO_RETENTION_PROVIDERS

    async def complete(
        self, messages: list[dict], *, model: str | None = None, max_tokens: int = 1024, **opts
    ) -> Completion:
        kwargs = {k: opts[k] for k in _GATEWAY_OPT_KEYS if k in opts}
        result = await self._gateway.complete(
            messages, config=self._config, model=model, max_tokens=max_tokens, **kwargs
        )
        return Completion(
            text=result.text,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
        )


class OpenCodeZenProvider:
    """Future heartbeat provider (opencode Zen) — stub until creds exist.

    Free Zen models may train on the data, so ``zero_retention`` is False. Enable
    it by implementing ``complete()`` against the Zen gateway once credentials are
    available; until then every call raises ``ProviderNotConfigured``.
    """

    name = "zen"
    zero_retention = False

    def __init__(self, config: "Config | None" = None):
        self._config = config

    async def complete(
        self, messages: list[dict], *, model: str | None = None, max_tokens: int = 1024, **opts
    ) -> Completion:
        raise ProviderNotConfigured("opencode Zen not configured")


def get_model_provider(config: "Config", *, name: str | None = None) -> ModelProvider:
    """Factory for the active ModelProvider. Defaults to the GatewayProvider.

    ``name="zen"`` returns the (not-yet-configured) opencode Zen stub.
    """
    if name == "zen":
        return OpenCodeZenProvider(config)
    return GatewayProvider(config)


# ── Voice pipeline service (Pipecat) ──────────────────────────────────────────

def get_pipeline_llm(config: "Config"):
    """Return the Pipecat LLM service for the voice pipeline."""
    provider = config.llm_provider

    if provider == "anthropic":
        from pipecat.services.anthropic.llm import AnthropicLLMService
        logger.info(f"LLM: Anthropic ({config.model})")
        return AnthropicLLMService(
            api_key=config.anthropic_api_key,
            settings=AnthropicLLMService.Settings(model=config.model),
        )

    elif provider == "openai":
        from pipecat.services.openai.llm import OpenAILLMService
        logger.info(f"LLM: OpenAI ({config.model})")
        return OpenAILLMService(
            api_key=config.openai_api_key,
            settings=OpenAILLMService.Settings(model=config.model),
        )

    elif provider == "openrouter":
        from pipecat.services.openrouter.llm import OpenRouterLLMService
        logger.info(f"LLM: OpenRouter ({config.model})")
        return OpenRouterLLMService(
            api_key=config.openrouter_api_key,
            settings=OpenRouterLLMService.Settings(model=config.model),
        )

    elif provider == "ollama":
        from pipecat.services.ollama.llm import OLLamaLLMService
        logger.info(f"LLM: Ollama ({config.model} @ {config.ollama_base_url})")
        return OLLamaLLMService(
            base_url=config.ollama_base_url,
            settings=OLLamaLLMService.Settings(model=config.model),
        )

    elif provider == "cerebras":
        from pipecat.services.cerebras.llm import CerebrasLLMService
        logger.info(f"LLM: Cerebras ({config.model})")
        return CerebrasLLMService(
            api_key=config.cerebras_api_key,
            settings=CerebrasLLMService.Settings(model=config.model),
        )

    else:  # groq (default)
        from pipecat.services.groq.llm import GroqLLMService
        logger.info(f"LLM: Groq ({config.model})")
        return GroqLLMService(
            api_key=config.groq_api_key,
            settings=GroqLLMService.Settings(model=config.model),
        )


# ── Agent async client ─────────────────────────────────────────────────────────

def get_agent_client(config: "Config"):
    """
    Return (async_client, provider_name) for use in BaseAgent.
    Groq, OpenAI, OpenRouter, Ollama, and Cerebras all use the OpenAI-compatible
    protocol so we use the `openai` package with a custom base_url.
    Anthropic uses its own package.
    """
    provider = config.llm_provider

    if provider == "anthropic":
        key = ("anthropic", config.anthropic_api_key)
        if key not in _agent_clients:
            from anthropic import AsyncAnthropic
            _agent_clients[key] = AsyncAnthropic(api_key=config.anthropic_api_key)
        return _agent_clients[key], "anthropic"

    elif provider == "openai":
        key = ("openai", config.openai_api_key, "")
        if key not in _agent_clients:
            from openai import AsyncOpenAI
            _agent_clients[key] = AsyncOpenAI(api_key=config.openai_api_key)
        return _agent_clients[key], "openai"

    elif provider == "openrouter":
        key = ("openai", config.openrouter_api_key, "https://openrouter.ai/api/v1")
        if key not in _agent_clients:
            from openai import AsyncOpenAI
            _agent_clients[key] = AsyncOpenAI(
                api_key=config.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
            )
        return _agent_clients[key], "openai"

    elif provider == "ollama":
        base = config.ollama_base_url + "/v1"
        key = ("openai", "ollama", base)
        if key not in _agent_clients:
            from openai import AsyncOpenAI
            _agent_clients[key] = AsyncOpenAI(api_key="ollama", base_url=base)
        return _agent_clients[key], "openai"

    elif provider == "cerebras":
        key = ("openai", config.cerebras_api_key, "https://api.cerebras.ai/v1")
        if key not in _agent_clients:
            from openai import AsyncOpenAI
            _agent_clients[key] = AsyncOpenAI(
                api_key=config.cerebras_api_key,
                base_url="https://api.cerebras.ai/v1",
            )
        return _agent_clients[key], "openai"

    else:  # groq
        key = ("openai", config.groq_api_key, "https://api.groq.com/openai/v1")
        if key not in _agent_clients:
            from openai import AsyncOpenAI
            _agent_clients[key] = AsyncOpenAI(
                api_key=config.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
            )
        return _agent_clients[key], "openai"
