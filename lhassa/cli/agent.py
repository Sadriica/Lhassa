"""
CLI agent — runs a prompt through Lhassa's existing tool-calling loop.

This reuses the core rather than reimplementing it:
  - ``gateway.complete_with_tools`` — the single LLM choke point (cache + daily
    budget + cost ledger) that every cloud call routes through. It owns provider
    selection (Anthropic or OpenAI-compatible) and all usage accounting.
  - the full ``lhassa.tools.registry`` tool set (schemas + handlers), so the CLI
    has exactly the same capabilities as the rest of the assistant.
  - ``PERSONA_BLOCK`` for personality, plus a short CLI-appropriate instruction
    (markdown is fine here — unlike the voice BASE_PROMPT's no-markdown rule).

The loop mirrors BaseAgent's two branches (Anthropic tool-use / OpenAI-compatible
function calling). The gateway records token usage in the ledger; here we just sum
what it reports per turn so the CLI can still print a per-run cost line. No voice
pipeline, STT, or TTS is imported.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import uuid
from dataclasses import dataclass

from lhassa.persona import PERSONA_BLOCK

# Safety cap on tool-calling loops — same env knob the agents use.
MAX_ITERATIONS = int(os.getenv("LHASSA_MAX_AGENT_ITERATIONS", "10"))

# Short, CLI-appropriate instruction layered on top of the shared persona.
# Markdown IS allowed here (the voice BASE_PROMPT forbids it — that rule is for
# spoken audio and is wrong for a terminal).
CLI_INSTRUCTION = """You are running as a terminal CLI assistant. Output is read \
in a terminal, so:
- Markdown is welcome: use headings, bullet lists, and fenced code blocks where \
they make the answer clearer.
- Be concise and direct. Lead with the answer; keep explanation tight.
- You have tools (shell, file read/write, web search, GitHub, calendar/email, \
background agents, and more). Use them when they genuinely help, then report the \
result plainly — don't narrate every step."""


def build_system_prompt() -> str:
    """Persona-consistent system prompt for the CLI (markdown allowed)."""
    return f"{PERSONA_BLOCK}\n\n{CLI_INSTRUCTION}"


@dataclass
class CliResult:
    """Result of one CLI turn — the reply plus its costing metadata."""

    response: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


# ── Tool plumbing (reuse the registry) ─────────────────────────────────────────

class _HandlerCollector:
    """Stand-in for an LLM service: collects the registry's handler functions."""

    def __init__(self) -> None:
        self.handlers: dict = {}

    def register_function(self, name: str, handler) -> None:
        self.handlers[name] = handler


_handlers_cache: dict | None = None


def _tool_handlers() -> dict:
    """All registry handlers, keyed by tool name (cached for the process)."""
    global _handlers_cache
    if _handlers_cache is None:
        from lhassa.tools.registry import register_tools

        collector = _HandlerCollector()
        register_tools(collector)
        _handlers_cache = collector.handlers
    return _handlers_cache


def _openai_tools() -> list[dict]:
    """Registry schemas in OpenAI function-calling format."""
    from lhassa.tools.registry import TOOLS

    return [
        {"type": "function", "function": fs.to_default_dict()}
        for fs in TOOLS.standard_tools
    ]


def _anthropic_tools() -> list[dict]:
    """Registry schemas in Anthropic tool-use format."""
    from lhassa.tools.registry import TOOLS

    out = []
    for fs in TOOLS.standard_tools:
        d = fs.to_default_dict()
        out.append(
            {
                "name": d["name"],
                "description": d["description"],
                "input_schema": d["parameters"],
            }
        )
    return out


async def _execute_tool(handlers: dict, name: str, args: dict) -> str:
    """Run a registry handler and capture what it passes to result_callback."""
    from pipecat.services.llm_service import FunctionCallParams

    handler = handlers.get(name)
    if handler is None:
        return f"Unknown tool: {name}"

    holder: dict = {"result": None}

    async def _callback(result, *a, **k):
        holder["result"] = result

    params = FunctionCallParams(
        function_name=name,
        tool_call_id=str(uuid.uuid4())[:8],
        arguments=args,
        llm=None,
        context=None,
        result_callback=_callback,
    )
    await handler(params)
    result = holder["result"]
    if result is None:
        return ""
    return result if isinstance(result, str) else str(result)


# ── Loops ──────────────────────────────────────────────────────────────────────

async def _run_openai(config, model: str, system: str, prompt: str, handlers: dict) -> CliResult:
    from lhassa.llm.gateway import gateway

    # System is passed to the gateway separately (it prepends it for OpenAI-compat).
    messages: list = [{"role": "user", "content": prompt}]
    tools = _openai_tools()
    in_tokens = out_tokens = 0
    cost = 0.0
    answer = ""

    for _ in range(MAX_ITERATIONS):
        turn = await gateway.complete_with_tools(
            messages,
            tools,
            config=config,
            model=model,
            system=system,
            max_tokens=4096,
        )
        in_tokens += turn.input_tokens
        out_tokens += turn.output_tokens
        cost += turn.cost_usd

        if turn.text:
            answer = turn.text

        if not turn.tool_calls:
            break

        assistant_tool_calls = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])},
            }
            for tc in turn.tool_calls
        ]
        messages.append(
            {"role": "assistant", "content": turn.text or None, "tool_calls": assistant_tool_calls}
        )
        for tc in turn.tool_calls:
            result = await _execute_tool(handlers, tc["name"], tc["input"])
            messages.append({"tool_call_id": tc["id"], "role": "tool", "content": result})

    return CliResult(answer, model, in_tokens, out_tokens, cost)


async def _run_anthropic(config, model: str, system: str, prompt: str, handlers: dict) -> CliResult:
    from lhassa.llm.gateway import gateway

    messages: list = [{"role": "user", "content": prompt}]
    tools = _anthropic_tools()
    in_tokens = out_tokens = 0
    cost = 0.0
    answer = ""

    for _ in range(MAX_ITERATIONS):
        turn = await gateway.complete_with_tools(
            messages,
            tools,
            config=config,
            model=model,
            system=system,
            max_tokens=4096,
        )
        in_tokens += turn.input_tokens
        out_tokens += turn.output_tokens
        cost += turn.cost_usd

        if turn.text:
            answer = turn.text

        if not turn.tool_calls:
            break

        assistant_content: list = []
        if turn.text:
            assistant_content.append({"type": "text", "text": turn.text})
        for tc in turn.tool_calls:
            assistant_content.append(
                {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]}
            )
        messages.append({"role": "assistant", "content": assistant_content})

        tool_result_content = []
        for tc in turn.tool_calls:
            result = await _execute_tool(handlers, tc["name"], tc["input"])
            tool_result_content.append(
                {"type": "tool_result", "tool_use_id": tc["id"], "content": result}
            )
        messages.append({"role": "user", "content": tool_result_content})

    return CliResult(answer, model, in_tokens, out_tokens, cost)


async def run_once(prompt: str, *, config=None) -> CliResult:
    """Run a single prompt through the tool loop and return the costed result.

    Tool handlers print dim status lines to stdout; we redirect stdout to stderr
    for the duration so the caller's stdout stays clean for the answer / JSON.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    system = build_system_prompt()

    # Chat via a LOCAL CLI provider (claude/codex/opencode): authenticated by the owner's
    # subscription, no API key. A tool-less one-shot — "just run claude and pass the message
    # through". Picked when the chat provider is set to one of these CLIs.
    if config.llm_provider in _CLI_CHAT_PROVIDERS:
        return await _run_cli_provider(config, system, prompt)

    # Provider shape selects the loop branch / tool format; the model round-trip
    # goes through the gateway (cache + budget + ledger).
    provider = "anthropic" if config.llm_provider == "anthropic" else "openai"
    handlers = _tool_handlers()

    with contextlib.redirect_stdout(sys.stderr):
        if provider == "anthropic":
            return await _run_anthropic(config, config.model, system, prompt, handlers)
        return await _run_openai(config, config.model, system, prompt, handlers)


# Chat providers backed by a local CLI (subscription auth, no API key).
_CLI_CHAT_PROVIDERS = ("claude", "codex", "opencode")


async def _run_cli_provider(config, system: str, prompt: str) -> "CliResult":
    """Chat through a local CLI provider (claude/codex/opencode) — tool-less, no API key."""
    from lhassa.llm.provider import ProviderNotConfigured, get_model_provider

    prov = get_model_provider(config, name=config.llm_provider)
    # Honor an EXPLICIT chat model (LHASSA_MODEL / the panel's "Chat model") so the owner
    # can pick e.g. claude-opus-4-8 / a codex model; otherwise pass None and let the CLI use
    # its own default. (config.model auto-detects an API default like a Groq id, which would
    # be wrong to hand a CLI — so only pass it when the owner set it on purpose.)
    chat_model = config.model if os.getenv("LHASSA_MODEL") else None
    try:
        comp = await prov.complete(
            [{"role": "user", "content": prompt}],
            model=chat_model,
            max_tokens=1024,
            system=system,
        )
    except ProviderNotConfigured as exc:
        return CliResult(
            f"({config.llm_provider} CLI is not available: {exc})",
            config.llm_provider, 0, 0, 0.0,
        )
    return CliResult(
        (comp.text or "").strip(),
        comp.model or config.llm_provider,
        comp.input_tokens,
        comp.output_tokens,
        comp.cost_usd,
    )
