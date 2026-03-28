"""
LLM provider factory — selects the right Pipecat service and async client
based on POTTS_LLM_PROVIDER.

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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from potts.config import Config


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
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(api_key=config.anthropic_api_key), "anthropic"

    elif provider == "openai":
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=config.openai_api_key), "openai"

    elif provider == "openrouter":
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=config.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
        ), "openai"

    elif provider == "ollama":
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key="ollama",  # Ollama ignores the key but requires a non-empty value
            base_url=config.ollama_base_url + "/v1",
        ), "openai"

    elif provider == "cerebras":
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=config.cerebras_api_key,
            base_url="https://api.cerebras.ai/v1",
        ), "openai"

    else:  # groq — use openai-compatible client for unified tool calling
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=config.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        ), "openai"
