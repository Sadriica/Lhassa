# SPDX-License-Identifier: AGPL-3.0-or-later
"""
CLI agent; runs a prompt through Lhassa's existing tool-calling loop.

This reuses the core rather than reimplementing it:
  - ``gateway.complete_with_tools``: the single LLM choke point (cache + daily
    budget + cost ledger) that every cloud call routes through. It owns provider
    selection (Anthropic or OpenAI-compatible) and all usage accounting.
  - the full ``lhassa.tools.registry`` tool set (schemas + handlers), so the CLI
    has exactly the same capabilities as the rest of the assistant.
  - ``PERSONA_BLOCK`` for personality, plus a short CLI-appropriate instruction
    (markdown is fine here; unlike the voice BASE_PROMPT's no-markdown rule).

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

from loguru import logger

from lhassa.persona import PERSONA_BLOCK

# Safety cap on tool-calling loops; same env knob the agents use, but a higher
# floor for THIS door. 10 was tuned for a question-and-answer turn and is simply
# not a budget for work: asked to create a brain or author a flow, the loop spent
# its whole allowance orienting and reported a plan instead of a result. Real
# self-modification turns finished at 20 and nothing needed more.
MAX_ITERATIONS = int(os.getenv("LHASSA_MAX_AGENT_ITERATIONS", "20"))

# A single tool result is clipped before it enters the transcript. A bare
# ``find /`` can return tens of KB; unclipped, every later turn re-pays for it and
# one question can cost 90k input tokens. Clipping is honest: the model is told
# the output was cut, so it narrows the search instead of assuming it saw everything.
MAX_TOOL_RESULT_CHARS = int(os.getenv("LHASSA_CLI_TOOL_RESULT_CHARS", "4000"))

# How much of the question/answer the episode row keeps. Long enough to be worth
# recalling, short enough that the log stays a log.
_EPISODE_CHARS = 2000

# How many past turns the REPL threads back in (user+assistant pairs).
HISTORY_TURNS = int(os.getenv("LHASSA_CLI_HISTORY_TURNS", "8"))

# Said when the tool budget runs out before an answer exists. Silence is the bug
# this replaces: the loop used to end mid-flight and print nothing at all.
_EXHAUSTED_NOTE = (
    "Heads up: I ran out of tool steps before finishing. Here is where I got to."
)

# Short, CLI-appropriate instruction layered on top of the shared persona.
# Markdown IS allowed here (the voice BASE_PROMPT forbids it; that rule is for
# spoken audio and is wrong for a terminal).
CLI_INSTRUCTION = """You are running as a terminal CLI assistant. Output is read \
in a terminal, so:
- Markdown is welcome: use headings, bullet lists, and fenced code blocks where \
they make the answer clearer.
- Be concise and direct. Lead with the answer; keep explanation tight.
- You have tools (shell, file read/write, web search, GitHub, calendar/email, \
background agents, and more). Use them when they genuinely help, then report the \
result plainly; don't narrate every step.
- PREFER your own registered tools over ad-hoc shell. If a tool already covers the \
job (tracking job applications, reminders, watches, calendar), call it instead of \
hunting the filesystem or writing a new script for what you already have.
- Say what you actually DID, and separate it from what you only PLAN to do. Never \
announce a file as written unless the write tool reported success.
- If a job needs a capability you do not have, SAY SO and propose the seam that \
would give it to you. Do not route around your own architecture to get it done: \
installing a systemd unit or a cron entry to schedule something you cannot \
schedule, editing a store by hand because no command writes it, or shelling out \
to fake a tool you lack. Those leave changes on the owner's machine that neither \
of you can see afterwards. "I need X, want me to build it?" is a better answer \
than a result obtained sideways.
- Never write in em dashes. Use ":", ";", "," or rewrite the sentence."""


def build_system_prompt() -> str:
    """Persona-consistent system prompt for the CLI (markdown allowed).

    The memory-less floor. ``system_prompt()`` layers the brain's own context on
    top of it; this stays the fallback for when that read degrades.
    """
    return f"{PERSONA_BLOCK}\n\n{CLI_INSTRUCTION}"


async def system_prompt(config, query: str | None = None) -> str:
    """The floor plus the brain's own memory context, layered for this question.

    The same seam every other door already uses (``memory/load.py``): identity
    (pinned facts + directivas) → recency → tags → the query-driven $0 layers
    (semantic, episodic recall, knowledge, graph). Without it the CLI thought with
    an empty head: asked about its own work it went hunting the filesystem with
    ``find /`` for facts it already held.

    All of it is local and costs no tokens beyond the prompt itself. Any failure
    degrades to ``build_system_prompt()``; the brain answers with less context,
    never with an error.
    """
    base = build_system_prompt()
    try:
        from lhassa.memory import load as memory_load

        ctx = await memory_load.load(
            getattr(config, "instance_id", None),
            token_budget=int(getattr(config, "context_token_budget", 2000) or 2000),
            query=query or None,
            config=config,
        )
        block = ctx.to_prompt()
    except Exception:  # a degraded memory read must never cost the answer
        return base

    # The same live self-card the chat door carries, rendered for THIS door.
    # Without it a terminal turn did not know its own action registry or CLI
    # surface and spent its whole tool budget on `lhassa --help` before it
    # could start the actual work; asked to create a brain, it ran out of
    # steps having done nothing. $0 and deterministic; degrades to nothing.
    card = ""
    try:
        from lhassa.notify import selfcard as _selfcard

        card = _selfcard.build(config, door="cli")
    except Exception as exc:
        logger.debug(f"cli: selfcard degraded to a no-op: {exc}")

    parts = [PERSONA_BLOCK, block, card, CLI_INSTRUCTION]
    body = "\n\n".join(p for p in parts if p)
    return body if (block or card) else base


@dataclass
class CliResult:
    """Result of one CLI turn; the reply plus its costing metadata."""

    response: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    # True when the tool loop hit MAX_ITERATIONS: the answer is a wrap-up, not a finish.
    truncated: bool = False
    # Names of the tools actually called this turn, in order (for the episode log).
    tools_used: tuple = ()


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


def _door_schemas() -> list[dict]:
    """The tool schemas THIS door sends, as plain dicts.

    Every schema is re-sent on every iteration of the tool loop, so the catalog is
    a fixed per-iteration tax (26 tools ~= 2.8k tokens). Two knobs shape it, both
    off by default so no capability disappears without the owner asking:

      ``LHASSA_CLI_TOOLS``          allow-list; when set, ONLY these are sent.
      ``LHASSA_CLI_TOOLS_EXCLUDE``  deny-list; defaults to the one tool that cannot
                                    serve a terminal turn (a screen grab has no
                                    reader here; the voice/desktop door keeps it).

    Filtering is name-based and forgiving: an unknown name in either list is
    ignored, and an allow-list that matches nothing degrades to the full catalog
    rather than leaving the model with no tools at all.
    """
    from lhassa.tools.schemas import TOOLS

    allow = {n.strip() for n in os.getenv("LHASSA_CLI_TOOLS", "").split(",") if n.strip()}
    deny = {
        n.strip()
        for n in os.getenv("LHASSA_CLI_TOOLS_EXCLUDE", "analyze_screen").split(",")
        if n.strip()
    }
    everything = [fs.to_default_dict() for fs in TOOLS.standard_tools]
    kept = [
        d for d in everything
        if (not allow or d["name"] in allow) and d["name"] not in deny
    ]
    return kept or everything


def _openai_tools() -> list[dict]:
    """Registry schemas in OpenAI function-calling format."""
    return [{"type": "function", "function": d} for d in _door_schemas()]


def _anthropic_tools() -> list[dict]:
    """Registry schemas in Anthropic tool-use format."""
    out = []
    for d in _door_schemas():
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
    try:
        from pipecat.services.llm_service import FunctionCallParams
    except ImportError:
        # pipecat ships only with the voice pack's runtime extra. Handlers read
        # just ``arguments`` and ``result_callback``, so a plain namespace with
        # the same fields serves the CLI loop on a core-only install.
        from types import SimpleNamespace as FunctionCallParams

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
    return _clip(result if isinstance(result, str) else str(result))


def _clip(text: str) -> str:
    """Cut one tool result down to size, saying so where it was cut."""
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    dropped = len(text) - MAX_TOOL_RESULT_CHARS
    return (
        text[:MAX_TOOL_RESULT_CHARS]
        + f"\n[output truncated: {dropped} more characters. Narrow the query if you need the rest.]"
    )


def _budget_nudge(messages: list, step: int) -> None:
    """Tell the model how many tool steps it has left, once it is running low.

    The loop had no budget signal at all: the model could not see the cap, so a
    cheap model asked to build something would explore until the steps ran out
    and then report a plan instead of a result ("give me one more turn"; there
    is no next turn). Two nudges, appended as ordinary user messages so both
    provider shapes accept them: one at the halfway mark, one near the end.
    Silent for short runs, and never a substitute for the wrap-up turn.
    """
    left = MAX_ITERATIONS - step - 1
    if MAX_ITERATIONS < 4 or left not in (MAX_ITERATIONS // 2, 2):
        return
    if left == 2:
        text = (
            f"Budget check: {left} tool steps left. Stop investigating and ACT now "
            "with what you already know. A finished attempt beats a perfect plan."
        )
    else:
        text = (
            f"Budget check: {left} of {MAX_ITERATIONS} tool steps left. If you are "
            "still exploring, start doing the actual work now."
        )
    messages.append({"role": "user", "content": text})


def _seed_messages(history, prompt: str) -> list:
    """Past turns (plain text, no tool blocks) followed by this turn's prompt.

    Plain ``{role, content}`` pairs are valid for BOTH provider shapes, so the same
    seed works for the Anthropic and the OpenAI-compatible loop. Tool calls from
    past turns are deliberately dropped: what the next turn needs is what was SAID,
    not a replay of every shell command.
    """
    messages: list = []
    for role, content in (history or [])[-(HISTORY_TURNS * 2):]:
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": prompt})
    return messages


# ── Loops ──────────────────────────────────────────────────────────────────────

async def _run_openai(
    config, model: str, system: str, prompt: str, handlers: dict, history=None
) -> CliResult:
    from lhassa.llm.gateway import gateway

    # System is passed to the gateway separately (it prepends it for OpenAI-compat).
    messages: list = _seed_messages(history, prompt)
    tools = _openai_tools()
    in_tokens = out_tokens = 0
    cost = 0.0
    answer = ""
    exhausted = True
    used: list[str] = []

    for step in range(MAX_ITERATIONS):
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
            exhausted = False
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
            used.append(tc["name"])
            result = await _execute_tool(handlers, tc["name"], tc["input"])
            messages.append({"tool_call_id": tc["id"], "role": "tool", "content": result})
        _budget_nudge(messages, step)

    if exhausted:
        answer, extra = await _wrapup(gateway, messages, config, model, system, answer)
        in_tokens += extra[0]
        out_tokens += extra[1]
        cost += extra[2]

    return CliResult(
        _final(answer), model, in_tokens, out_tokens, cost,
        truncated=exhausted, tools_used=tuple(used),
    )


async def _wrapup(gateway, messages, config, model, system, answer):
    """One last, TOOL-LESS turn so a step-capped run still says what it found.

    Costs one small call and is only reached when the loop ran out of iterations.
    Any failure keeps whatever partial answer exists; the REPL never goes silent.
    """
    messages = messages + [
        {
            "role": "user",
            "content": (
                "You have run out of tool steps. Answer NOW with what you already have: "
                "say what you found, what you actually did or did not do, and the single "
                "next step. Do not request more tools."
            ),
        }
    ]
    try:
        turn = await gateway.complete_with_tools(
            messages, [], config=config, model=model, system=system, max_tokens=1024
        )
    except Exception:
        return answer, (0, 0, 0.0)
    text = (turn.text or "").strip()
    if text:
        answer = f"{_EXHAUSTED_NOTE}\n\n{text}"
    return answer, (turn.input_tokens, turn.output_tokens, turn.cost_usd)


def _final(answer: str) -> str:
    """The turn's answer, scrubbed and never empty; silence reads as a crash.

    ``scrub_dashes`` is the owner's no-em-dash rule enforced rather than
    requested, the same treatment the notify and digest passes already give
    model-written text. The CLI was the loudest surface still exempt: every
    terminal answer arrived full of the one punctuation mark the rule forbids,
    because a prompt asking nicely does not survive contact with a model.
    """
    from lhassa import persona

    text = persona.scrub_dashes((answer or "").strip())
    return text or (
        "I could not produce an answer this turn: the tool loop ended without one. "
        "Ask again, narrower."
    )


async def _run_anthropic(
    config, model: str, system: str, prompt: str, handlers: dict, history=None
) -> CliResult:
    from lhassa.llm.gateway import gateway

    messages: list = _seed_messages(history, prompt)
    tools = _anthropic_tools()
    in_tokens = out_tokens = 0
    cost = 0.0
    answer = ""
    exhausted = True
    used: list[str] = []

    for step in range(MAX_ITERATIONS):
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
            exhausted = False
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
            used.append(tc["name"])
            result = await _execute_tool(handlers, tc["name"], tc["input"])
            tool_result_content.append(
                {"type": "tool_result", "tool_use_id": tc["id"], "content": result}
            )
        # The nudge rides INSIDE this user turn as a trailing text block: the
        # Anthropic shape wants tool results and their turn to be one message,
        # not two consecutive user messages.
        nudge: list = []
        _budget_nudge(nudge, step)
        if nudge:
            tool_result_content.append({"type": "text", "text": nudge[0]["content"]})
        messages.append({"role": "user", "content": tool_result_content})

    if exhausted:
        answer, extra = await _wrapup(gateway, messages, config, model, system, answer)
        in_tokens += extra[0]
        out_tokens += extra[1]
        cost += extra[2]

    return CliResult(
        _final(answer), model, in_tokens, out_tokens, cost,
        truncated=exhausted, tools_used=tuple(used),
    )


async def run_once(prompt: str, *, config=None, history=None) -> CliResult:
    """Run a single prompt through the tool loop and return the costed result.

    Tool handlers print dim status lines to stdout; we redirect stdout to stderr
    for the duration so the caller's stdout stays clean for the answer / JSON.

    ``history`` is an optional list of ``(role, text)`` pairs from earlier turns of
    the same conversation (the REPL keeps it in process memory, like the Telegram
    door does). Without it every turn starts amnesiac, which is why "did you create
    it?" used to get "I have no memory of previous sessions".

    Every turn leaves ONE $0 Episode (``despertar_por="cli"``). Before this, the CLI
    was the only door that left no trace: 0 of 1672 episodes, so episodic recall
    could never find terminal work and the utility tribunal read CLI-only
    capabilities as dormant.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    instance_id = getattr(config, "instance_id", None) or "inst_personal"
    cached = await _cache_lookup(config, instance_id, prompt, history)
    if cached is not None:
        await _log_episode(config, instance_id, prompt, cached, cache_hit=True)
        return cached

    system = await system_prompt(config, prompt)

    # Chat via a LOCAL CLI provider (claude/codex/opencode): authenticated by the owner's
    # subscription, no API key. A tool-less one-shot; "just run claude and pass the message
    # through". Picked when the chat provider is set to one of these CLIs.
    if config.llm_provider in _CLI_CHAT_PROVIDERS:
        result = await _run_cli_provider(config, system, prompt, history)
    else:
        # Provider shape selects the loop branch / tool format; the model round-trip
        # goes through the gateway (cache + budget + ledger).
        provider = "anthropic" if config.llm_provider == "anthropic" else "openai"
        handlers = _tool_handlers()
        # Tell the audit trail which door is running these tools; the handler
        # families are shared with voice and would otherwise file it as "voice_tool".
        from lhassa.tools.handlers._shared import reset_door, set_door

        door = set_door("cli_tool")
        try:
            with contextlib.redirect_stdout(sys.stderr):
                if provider == "anthropic":
                    result = await _run_anthropic(
                        config, config.model, system, prompt, handlers, history
                    )
                else:
                    result = await _run_openai(
                        config, config.model, system, prompt, handlers, history
                    )
        finally:
            reset_door(door)

    await _log_episode(config, instance_id, prompt, result, cache_hit=False)
    await _cache_store(config, instance_id, prompt, result, history)
    return result


# ── The turn's own record: one episode, one $0 cache probe ─────────────────────

async def _log_episode(config, instance_id: str, prompt: str, result: CliResult, *,
                       cache_hit: bool) -> None:
    """Append one Episode for this CLI turn. Best-effort; never breaks the answer.

    The question rides in ``accion`` and the answer in ``resultado``: both are FTS5
    -indexed columns, so a later "what did I ask about X?" actually finds this turn.
    """
    try:
        from lhassa.core import db
        from lhassa.core.episode import Episode

        await db.init_db()  # idempotent; the CLI may be the first thing to wake
        decision = "cache-hit: served a past answer" if cache_hit else (
            f"think: {result.model}"
            + (f" | tools: {', '.join(result.tools_used)}" if result.tools_used else "")
            + (" | step cap reached" if result.truncated else "")
        )
        ep = Episode.begin(despertar_por="cli", instance_id=instance_id)
        ep.finish(
            decision=decision,
            accion=prompt[:_EPISODE_CHARS],
            resultado=result.response[:_EPISODE_CHARS],
            costo_usd=result.cost_usd,
        )
        await ep.append()
        await _log_capability_uses(instance_id, ep.id, result)
    except Exception as exc:  # a log write must never cost the owner their answer
        logger.debug(f"cli: episode log degraded to a no-op: {exc}")


#: Terminal TOOL name -> the capability (ACTIONS registry name) it is a door onto.
#: Two namespaces meet here: the tool catalog the model calls and the action
#: registry the rest of the brain reasons about. Most tools have no action twin
#: (a web search, a GitHub read) and are deliberately absent; only a genuine
#: same-capability pair belongs in this map, which is why it is short, explicit,
#: and hand-maintained rather than inferred from names.
_TOOL_CAPABILITY: dict = {
    "track_postulaciones": "tracking_postulaciones",
    "shell": "run_command",
    "run_privileged": "run_command",
    "read_file": "read_file",
    "schedule_reminder": "send_reminder",
    "read_calendar": "calendar_sync",
    "create_event": "calendar_sync",
}


async def _log_capability_uses(instance_id: str, turn_episode_id: str,
                               result: CliResult) -> None:
    """One $0 Episode per capability this turn actually exercised.

    ``agents/utility.py`` answers "is this capability still used?" by grouping
    the episode log on ``accion``, which works because everywhere else in the
    brain a capability leaves an episode NAMING itself. The terminal broke that
    rule: its turn episode puts the owner's QUESTION in ``accion`` (deliberately,
    so FTS5 recall can find it) and the tools it ran end up as prose in
    ``decision``, where no aggregate can see them. So the tribunal read every
    capability as never once owner-driven and began parking proposals to retire
    things the owner uses daily.

    This restores the invariant rather than teaching the tribunal to parse prose.
    Deduplicated per turn (eight ``shell`` calls are one use of ``run_command``),
    $0, and best-effort: losing these rows costs accounting, never the answer.
    """
    if not result.tools_used:
        return
    seen: list = []
    for tool in result.tools_used:
        capability = _TOOL_CAPABILITY.get(tool)
        if capability and capability not in seen:
            seen.append(capability)
    if not seen:
        return
    try:
        from lhassa.core.episode import Episode

        for capability in seen:
            ep = Episode.begin(despertar_por="cli", instance_id=instance_id)
            ep.finish(
                decision=f"capability use recorded from turn {turn_episode_id}",
                accion=capability,
                resultado=f"{capability}: exercised from the terminal",
                costo_usd=0.0,
            )
            await ep.append()
    except Exception as exc:
        logger.debug(f"cli: capability-use log degraded to a no-op: {exc}")


async def _cache_lookup(config, instance_id: str, prompt: str, history) -> "CliResult | None":
    """A $0 answer-cache hit for a stable question, or None.

    Only a FIRST turn is eligible: mid-conversation, "and that one?" means nothing
    without what came before, so a similarity hit there would answer the wrong
    question. The intent gate is the same $0 regex router the Telegram door uses;
    ``live_data`` (news, prices, "hoy") is never cached.
    """
    if history or not getattr(config, "answer_cache_enabled", True):
        return None
    try:
        from lhassa.memory import answer_cache
        from lhassa.notify.intent import classify

        if classify(prompt) != "cacheable":
            return None
        hit = await answer_cache.lookup(
            instance_id,
            prompt,
            threshold=float(getattr(config, "answer_cache_threshold", 0.90) or 0.90),
            config=config,
        )
    except Exception as exc:  # a broken cache degrades to a normal, paid think
        logger.debug(f"cli: answer cache lookup degraded to a miss: {exc}")
        return None
    if hit is None:
        return None
    answer, _similarity = hit
    return CliResult(answer, "cache", 0, 0, 0.0)


async def _cache_store(config, instance_id: str, prompt: str, result: CliResult, history) -> None:
    """Store a stable first-turn answer for next time. Best-effort, never raises."""
    if history or result.truncated or not getattr(config, "answer_cache_enabled", True):
        return
    try:
        from lhassa.memory import answer_cache
        from lhassa.notify.intent import classify

        if classify(prompt) == "cacheable":
            await answer_cache.store(instance_id, prompt, result.response, config=config)
    except Exception as exc:
        logger.debug(f"cli: answer cache store degraded to a no-op: {exc}")


# Chat providers backed by a local CLI (subscription auth, no API key).
_CLI_CHAT_PROVIDERS = ("claude", "codex", "opencode")


async def _run_cli_provider(config, system: str, prompt: str, history=None) -> "CliResult":
    """Chat through a local CLI provider (claude/codex/opencode); tool-less, no API key."""
    from lhassa.llm.provider import ProviderNotConfigured, get_model_provider

    prov = get_model_provider(config, name=config.llm_provider)
    # Honor an EXPLICIT chat model (LHASSA_MODEL / the panel's "Chat model") so the owner
    # can pick e.g. claude-opus-4-8 / a codex model; otherwise pass None and let the CLI use
    # its own default. (config.model auto-detects an API default like a Groq id, which would
    # be wrong to hand a CLI; so only pass it when the owner set it on purpose.)
    chat_model = config.model if os.getenv("LHASSA_MODEL") else None
    try:
        comp = await prov.complete(
            _seed_messages(history, prompt),
            model=chat_model,
            # Match the gateway path's cap. At 1024 a long terminal answer (a plan,
            # a code block) was cut mid-sentence and looked like a crash.
            max_tokens=4096,
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
