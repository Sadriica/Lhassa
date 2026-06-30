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

import asyncio
import json
import re
from dataclasses import dataclass
from loguru import logger
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol, runtime_checkable

from lhassa.registry import Registry

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


# Timeout for a single opencode beat: long enough for a free-tier model, short
# enough that a stuck CLI degrades the heartbeat instead of hanging it.
_OPENCODE_TIMEOUT = 120.0

# Strips terminal colour codes from a fallback (non-JSON) stdout.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clean_stdout(stdout: str) -> str:
    """Last-resort text: strip ANSI codes and surrounding whitespace."""
    return _ANSI_RE.sub("", stdout).strip()


def _iter_json_events(stdout: str):
    """Yield JSON values from an opencode ``--format json`` stdout, defensively.

    [SUPUESTO] The exact opencode JSON shape is not pinned. We accept three forms
    and tolerate anything else: (1) the whole stdout is a single JSON object;
    (2) the whole stdout is a JSON array of events; (3) line-delimited JSON
    (NDJSON), skipping any non-JSON line (banners, logs, blank lines).
    """
    stdout = stdout.strip()
    if not stdout:
        return
    try:
        whole = json.loads(stdout)
    except Exception:
        whole = None
    if whole is not None:
        if isinstance(whole, list):
            yield from whole
        else:
            yield whole
        return
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def _extract_text(obj: dict) -> str | None:
    """Pull the most plausible assistant text from one event dict.

    [SUPUESTO] Field names are assumed. We look, in order, at: ``parts`` (a list
    of ``{"type":"text","text":...}``), a ``content`` string or list, a flat
    ``text`` string, a streamed ``part``/``delta`` text, and a nested
    ``message`` object (recursing once).
    """
    # Assistant message carrying a list of typed parts.
    parts = obj.get("parts")
    if isinstance(parts, list):
        texts = [
            p.get("text")
            for p in parts
            if isinstance(p, dict) and isinstance(p.get("text"), str)
        ]
        if texts:
            return "".join(texts)
    # OpenAI-ish content: a string or a list of text segments.
    content = obj.get("content")
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        texts = []
        for p in content:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                texts.append(p["text"])
            elif isinstance(p, str):
                texts.append(p)
        if texts:
            return "".join(texts)
    # Flat text field.
    text = obj.get("text")
    if isinstance(text, str) and text:
        return text
    # Streamed deltas: {"part": {"text": ...}} / {"part": "..."} / {"delta": {...}}.
    for key in ("part", "delta"):
        v = obj.get(key)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict) and isinstance(v.get("text"), str) and v["text"]:
            return v["text"]
    # Nested assistant message.
    msg = obj.get("message")
    if isinstance(msg, dict):
        return _extract_text(msg)
    return None


def _extract_usage(obj: dict) -> tuple[float, int, int]:
    """Pull (cost_usd, input_tokens, output_tokens) from one event, if present.

    Verified live: a ``step_finish`` event nests the totals under ``part`` —
    ``{"part": {"tokens": {"input": N, "output": M, ...}, "cost": 0}}`` — so we look
    at both the event and its ``part`` child. Several common key spellings are still
    accepted defensively for other opencode/model versions.
    """
    # Look at the event itself and its nested ``part`` (where step_finish totals live).
    src = obj
    part = obj.get("part")
    if isinstance(part, dict) and (part.get("tokens") or part.get("cost") is not None):
        src = part

    cost = 0.0
    for key in ("cost", "cost_usd"):
        v = src.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            cost = float(v)
    usage = src.get("usage")
    if not isinstance(usage, dict):
        usage = src.get("tokens")
    in_tok = out_tok = 0
    if isinstance(usage, dict):
        in_tok = _int(
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or usage.get("input")
            or 0
        )
        out_tok = _int(
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or usage.get("output")
            or 0
        )
        c = usage.get("cost")
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            cost = float(c)
    return cost, in_tok, out_tok


def _parse_opencode_json(stdout: str) -> tuple[str, float, int, int]:
    """Parse opencode JSON stdout into (text, cost_usd, input_tokens, output_tokens).

    Defensive: a complete assistant message (``parts``/``content``/``text``) wins
    over streamed deltas; if only deltas are seen they are concatenated; if
    nothing parses as JSON the cleaned raw stdout is used as the text.
    """
    final_text: str | None = None
    chunks: list[str] = []
    cost = 0.0
    in_tok = out_tok = 0
    parsed_any = False

    for obj in _iter_json_events(stdout):
        if not isinstance(obj, dict):
            continue
        parsed_any = True
        c, i, o = _extract_usage(obj)
        if c:
            cost = c
        if i:
            in_tok = i
        if o:
            out_tok = o
        text = _extract_text(obj)
        if text:
            # A delta event carries a partial chunk; anything else is treated as
            # a (possibly final) full message and overwrites. [SUPUESTO]
            if ("part" in obj or "delta" in obj) and "parts" not in obj:
                chunks.append(text)
            else:
                final_text = text

    if final_text:
        result = final_text
    elif chunks:
        result = "".join(chunks)
    elif not parsed_any:
        result = _clean_stdout(stdout)
    else:
        result = ""
    return result, cost, in_tok, out_tok


class OpenCodeProvider:
    """Heartbeat/routine provider backed by the local **opencode CLI**.

    The owner has the opencode CLI (no Zen), so routine thinking can run through
    it using the owner's existing opencode credentials. ``complete()`` shells
    ``opencode run --format json -m <model> <prompt>`` via a subprocess and parses
    the JSON event stream for the assistant's text and (if reported) cost/usage.

    It is a cloud subscription (inference happens upstream), so ``zero_retention``
    is False and ``is_local`` is False — the router still wraps it in the
    pseudonymization hook when ``pseudonymize_remote`` is on. A non-zero exit, a
    missing binary, a timeout, or empty output raises ``ProviderNotConfigured`` so
    the existing degradation path handles it rather than crashing the heartbeat.
    """

    name = "opencode"
    zero_retention = False
    is_local = False

    def __init__(self, config: "Config", *, runner: Any | None = None):
        self._config = config
        # Subprocess seam: an async callable ``(argv) -> (returncode, stdout_bytes,
        # stderr_bytes)``. Injected by tests so they never spawn real opencode.
        self._runner = runner

    @staticmethod
    def _build_prompt(messages: list[dict], system: Any) -> str:
        """Flatten chat messages into a single prompt string for ``opencode run``.

        A ``system`` opt (and any system-role message) is prepended as plain
        guidance; user/assistant turns are lightly labelled for multi-turn context.
        """
        parts: list[str] = []
        if isinstance(system, str) and system:
            parts.append(system)
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if not content:
                continue
            if role == "system":
                parts.append(content)
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
            else:
                parts.append(f"User: {content}")
        return "\n\n".join(parts)

    async def _run(self, argv: list[str]) -> tuple[int | None, bytes, bytes]:
        if self._runner is not None:
            return await self._runner(argv)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_OPENCODE_TIMEOUT
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise
        return proc.returncode, stdout, stderr

    async def complete(
        self, messages: list[dict], *, model: str | None = None, max_tokens: int = 1024, **opts
    ) -> Completion:
        model = model or (getattr(self._config, "opencode_model", "") or "")
        binary = (getattr(self._config, "opencode_bin", "") or "") or "opencode"
        prompt = self._build_prompt(messages, opts.get("system"))
        # argv (verified live): ``opencode run --pure --format json -m <model> <prompt>``.
        # ``--pure`` skips external plugins/MCP — without it a routine beat takes >120s
        # and can hang on plugin/network startup; with it the same call returns in ~8s.
        argv = [binary, "run", "--pure", "--format", "json", "-m", model, prompt]

        try:
            returncode, stdout_b, stderr_b = await asyncio.wait_for(
                self._run(argv), timeout=_OPENCODE_TIMEOUT
            )
        except FileNotFoundError as exc:
            raise ProviderNotConfigured(
                f"opencode unavailable: binary {binary!r} not found ({exc})"
            ) from exc
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise ProviderNotConfigured(
                f"opencode unavailable: timed out after {_OPENCODE_TIMEOUT:.0f}s"
            ) from exc

        stdout = (stdout_b or b"").decode("utf-8", "replace")
        stderr = (stderr_b or b"").decode("utf-8", "replace")
        if returncode not in (0, None):
            detail = (stderr or stdout).strip()[:300]
            raise ProviderNotConfigured(
                f"opencode unavailable: exit {returncode}: {detail}"
            )

        text, cost, in_tok, out_tok = _parse_opencode_json(stdout)
        if not text.strip():
            raise ProviderNotConfigured("opencode unavailable: empty output")

        return Completion(
            text=text,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
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


# ── provider factory registry (the §2.2 axis-1 swap point) ────────────────────
#
# The set of named provider FACTORIES lives in the shared keystone ``Registry``
# (the same primitive that backs actions/channels/commands/harnesses), so a plugin
# can drop in a new ModelProvider with ``@register_provider("x")`` WITHOUT editing
# this file — the elastic/modular principle. A factory is a ``(config) -> provider``
# callable; ``get_model_provider`` routes through it, preserving the exact historical
# behaviour (default → GatewayProvider; the named stubs/providers as before).
ProviderFactory = Callable[["Config"], ModelProvider]
PROVIDERS: Registry[ProviderFactory] = Registry("provider")

# The fallback key used when ``name`` is None or unknown (the historical default).
_DEFAULT_PROVIDER = "default"


def register_provider(
    name: str, factory: Optional[ProviderFactory] = None, *, override: bool = False
):
    """Register a ModelProvider factory under ``name`` — a wrapper over :data:`PROVIDERS`.

    Usable as a **decorator** (``@register_provider("x")`` on a ``(config) -> provider``
    factory) or a **direct call** (``register_provider("x", factory)``). A duplicate
    ``name`` raises unless ``override=True`` — so two plugins can never silently
    clobber each other. This is the drop-in seam for adding a provider without
    touching core.
    """
    return PROVIDERS.register(name, factory, override=override)


# Register the built-in factories. ``default`` (GatewayProvider) is the fallback.
register_provider(_DEFAULT_PROVIDER, lambda config: GatewayProvider(config))
register_provider("local", lambda config: LocalModelProvider(config))
register_provider("opencode", lambda config: OpenCodeProvider(config))
register_provider("zen", lambda config: OpenCodeZenProvider(config))


def get_model_provider(config: "Config", *, name: str | None = None) -> ModelProvider:
    """Factory for the active ModelProvider. Defaults to the GatewayProvider.

    ``name="zen"`` returns the (not-yet-configured) opencode Zen stub.
    ``name="local"`` returns the LOCAL OpenAI-compatible heartbeat provider.
    ``name="opencode"`` returns the opencode-CLI heartbeat provider.

    Routing goes through the :data:`PROVIDERS` registry; an absent or unknown
    ``name`` falls back to the ``default`` factory (GatewayProvider), preserving
    the historical behaviour exactly.
    """
    factory = PROVIDERS.get_or_none(name) if name else None
    if factory is None:
        factory = PROVIDERS.get(_DEFAULT_PROVIDER)
    return factory(config)


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
