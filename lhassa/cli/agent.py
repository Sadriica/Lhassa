"""
CLI agent — runs a prompt through Lhassa's existing tool-calling loop.

This reuses the core rather than reimplementing it:
  - ``get_agent_client(config)``  — the same provider-agnostic async client the
    voice pipeline and BaseAgent use (Anthropic or OpenAI-compatible).
  - the full ``lhassa.tools.registry`` tool set (schemas + handlers), so the CLI
    has exactly the same capabilities as the rest of the assistant.
  - ``PERSONA_BLOCK`` for personality, plus a short CLI-appropriate instruction
    (markdown is fine here — unlike the voice BASE_PROMPT's no-markdown rule).
  - ``estimate_cost`` from the LLM gateway to price the call.

The loop mirrors BaseAgent's two branches (Anthropic tool-use / OpenAI-compatible
function calling) but additionally accumulates token usage so the CLI can show a
cost line. No voice pipeline, STT, or TTS is imported.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import uuid
from dataclasses import dataclass

from lhassa.llm.gateway import estimate_cost
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

async def _run_openai(client, model: str, system: str, prompt: str, handlers: dict) -> CliResult:
    messages: list = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    tools = _openai_tools()
    in_tokens = out_tokens = 0
    answer = ""

    for _ in range(MAX_ITERATIONS):
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=4096,
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            in_tokens += getattr(usage, "prompt_tokens", 0) or 0
            out_tokens += getattr(usage, "completion_tokens", 0) or 0

        msg = resp.choices[0].message
        finish = resp.choices[0].finish_reason

        if msg.content:
            answer = msg.content

        if finish == "stop" or not getattr(msg, "tool_calls", None):
            break

        tool_results = []
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError as e:
                tool_results.append(
                    {"tool_call_id": tc.id, "role": "tool", "content": f"Error: invalid tool arguments — {e}"}
                )
                continue
            result = await _execute_tool(handlers, tc.function.name, args)
            tool_results.append({"tool_call_id": tc.id, "role": "tool", "content": result})

        messages.append(msg)
        messages.extend(tool_results)

    return CliResult(answer, model, in_tokens, out_tokens, estimate_cost(model, in_tokens, out_tokens))


async def _run_anthropic(client, model: str, system: str, prompt: str, handlers: dict) -> CliResult:
    messages: list = [{"role": "user", "content": prompt}]
    tools = _anthropic_tools()
    in_tokens = out_tokens = 0
    answer = ""

    for _ in range(MAX_ITERATIONS):
        resp = await client.messages.create(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=4096,
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            in_tokens += getattr(usage, "input_tokens", 0) or 0
            out_tokens += getattr(usage, "output_tokens", 0) or 0

        text_parts = []
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(block)

        if text_parts:
            answer = "".join(text_parts)

        if resp.stop_reason == "end_turn" or not tool_calls:
            break

        messages.append({"role": "assistant", "content": resp.content})
        tool_result_content = []
        for tc in tool_calls:
            result = await _execute_tool(handlers, tc.name, tc.input)
            tool_result_content.append(
                {"type": "tool_result", "tool_use_id": tc.id, "content": result}
            )
        messages.append({"role": "user", "content": tool_result_content})

    return CliResult(answer, model, in_tokens, out_tokens, estimate_cost(model, in_tokens, out_tokens))


async def run_once(prompt: str, *, config=None) -> CliResult:
    """Run a single prompt through the tool loop and return the costed result.

    Tool handlers print dim status lines to stdout; we redirect stdout to stderr
    for the duration so the caller's stdout stays clean for the answer / JSON.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    from lhassa.llm.provider import get_agent_client

    client, provider = get_agent_client(config)
    handlers = _tool_handlers()
    system = build_system_prompt()

    with contextlib.redirect_stdout(sys.stderr):
        if provider == "anthropic":
            return await _run_anthropic(client, config.model, system, prompt, handlers)
        return await _run_openai(client, config.model, system, prompt, handlers)
