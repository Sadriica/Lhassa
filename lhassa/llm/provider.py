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

from loguru import logger
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lhassa.config import Config

# Module-level cache — reuses connection pools across agent instances
_agent_clients: dict[tuple, Any] = {}


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
