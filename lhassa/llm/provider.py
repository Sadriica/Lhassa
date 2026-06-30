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
    # Marker so callers can introspect *where* a provider runs without importing
    # a concrete class. A local backend sets this True; everything else defaults
    # to False. Used by the router to relax the pseudonymization hook (§3-Fase 6).
    is_local: bool = False

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
    is_local = False

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
    is_local = False

    def __init__(self, config: "Config | None" = None):
        self._config = config

    async def complete(
        self, messages: list[dict], *, model: str | None = None, max_tokens: int = 1024, **opts
    ) -> Completion:
        raise ProviderNotConfigured("opencode Zen not configured")


# Short timeout: a local heartbeat must fail fast and degrade, not hang.
_LOCAL_TIMEOUT = 30.0


class LocalModelProvider:
    """Heartbeat/routine provider backed by a LOCAL OpenAI-compatible server.

    Talks to ``{local_model_url}/chat/completions`` (Ollama, llama.cpp, LM Studio,
    …) over httpx, with no API key. Because inference runs on this machine the
    data never leaves the box, so ``zero_retention`` is True and ``is_local`` is
    True — the router uses the latter to relax the pseudonymization hook.

    Inference is free, so every ``Completion`` reports ``cost_usd=0.0``. On a
    connection failure, a non-200 response, or an unparseable body it raises
    ``ProviderNotConfigured`` so the existing router/step degradation path kicks
    in instead of crashing the heartbeat.
    """

    name = "local"
    zero_retention = True
    is_local = True

    def __init__(self, config: "Config", *, client_factory: Any | None = None):
        self._config = config
        # Injectable for tests (an httpx.AsyncClient over a MockTransport); in
        # production a fresh short-timeout client per call.
        self._client_factory = client_factory

    def _url(self) -> str:
        url = (getattr(self._config, "local_model_url", "") or "").strip()
        if not url:
            raise ProviderNotConfigured(
                "local model not configured (set LHASSA_LOCAL_MODEL_URL)"
            )
        return url

    async def complete(
        self, messages: list[dict], *, model: str | None = None, max_tokens: int = 1024, **opts
    ) -> Completion:
        url = self._url()
        endpoint = url.rstrip("/") + "/chat/completions"
        model = model or (getattr(self._config, "local_model", "") or "") or "local"

        payload_messages = list(messages)
        system = opts.get("system")
        if isinstance(system, str) and system:
            payload_messages = [{"role": "system", "content": system}, *payload_messages]

        body: dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
        }
        if "temperature" in opts and opts["temperature"] is not None:
            body["temperature"] = opts["temperature"]

        import httpx

        if self._client_factory is not None:
            client = self._client_factory()
        else:
            client = httpx.AsyncClient(timeout=_LOCAL_TIMEOUT)

        try:
            try:
                resp = await client.post(endpoint, json=body)
            except Exception as exc:  # connection refused / timeout / DNS …
                raise ProviderNotConfigured(
                    f"local model unreachable at {url}: {exc}"
                ) from exc
            if resp.status_code != 200:
                raise ProviderNotConfigured(
                    f"local model unreachable at {url}: HTTP {resp.status_code}"
                )
            try:
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
            except Exception as exc:
                raise ProviderNotConfigured(
                    f"local model unreachable at {url}: bad response ({exc})"
                ) from exc
        finally:
            aclose = getattr(client, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass

        usage = data.get("usage") or {}
        return Completion(
            text=text or "",
            model=data.get("model") or model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cost_usd=0.0,
        )


class PseudonymizingProvider:
    """Wraps a REMOTE ModelProvider to scrub PII before it leaves the machine.

    Before ``complete()`` it pseudonymizes the ``messages`` (and any ``system``
    opt) with a single shared mapping; after, it de-pseudonymizes the returned
    text so the caller sees the real values back. ``name``/``zero_retention``
    proxy the wrapped provider; ``is_local`` is False (a wrapped provider is, by
    construction, the remote path). This enforces "pseudonymize before sending to
    a non-local provider" at the provider boundary, with no change to core/step.
    """

    is_local = False

    def __init__(self, wrapped: ModelProvider):
        self._wrapped = wrapped

    @property
    def name(self) -> str:
        return getattr(self._wrapped, "name", "wrapped")

    @property
    def zero_retention(self) -> bool:
        return bool(getattr(self._wrapped, "zero_retention", False))

    async def complete(
        self, messages: list[dict], *, model: str | None = None, max_tokens: int = 1024, **opts
    ) -> Completion:
        from lhassa.privacy import depseudonymize, pseudonymize_messages

        system = opts.get("system")
        # Scrub system + messages together so one shared mapping covers both
        # (avoids placeholder collisions between independent scrubs).
        combo = list(messages)
        has_system = isinstance(system, str) and bool(system)
        if has_system:
            combo = [*combo, {"role": "system", "content": system}]
        scrubbed_combo, mapping = pseudonymize_messages(combo)

        if has_system:
            scrubbed_messages = scrubbed_combo[:-1]
            opts = dict(opts)
            opts["system"] = scrubbed_combo[-1].get("content", system)
        else:
            scrubbed_messages = scrubbed_combo

        result = await self._wrapped.complete(
            scrubbed_messages, model=model, max_tokens=max_tokens, **opts
        )
        result.text = depseudonymize(result.text, mapping)
        return result


def get_model_provider(config: "Config", *, name: str | None = None) -> ModelProvider:
    """Factory for the active ModelProvider. Defaults to the GatewayProvider.

    ``name="zen"`` returns the (not-yet-configured) opencode Zen stub.
    ``name="local"`` returns the LOCAL OpenAI-compatible heartbeat provider.
    """
    if name == "zen":
        return OpenCodeZenProvider(config)
    if name == "local":
        return LocalModelProvider(config)
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
