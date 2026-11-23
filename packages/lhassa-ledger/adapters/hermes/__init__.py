# SPDX-License-Identifier: AGPL-3.0-or-later
"""agent-ledger: a Hermes plugin that writes every metered call and tool use
into a host-neutral append-only ledger.

Hermes already knows what its own agent spent. What it cannot know is what
the same owner spent in Claude Code inside an Orca worktree ten minutes
earlier. This plugin writes Hermes' side of that answer into a shared ledger
that other hosts write to as well.

Enable it with:

    hermes plugins enable observability/agent-ledger

The plugin is inert until enabled, holds no credentials, makes no network
calls, and never raises into a turn: on any internal failure it logs once and
lets the agent carry on.

Install layout: this package is symlinked (or copied) into
``~/.hermes/hermes-agent/plugins/observability/agent-ledger``. The ledger
library itself lives outside that tree, so ``AGENT_LEDGER_HOME`` points at
the repo checkout when the package is not already importable.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HOST = "hermes"

# Bound the in-flight tool timers. A tool call whose post hook never fires
# (interrupted turn, killed process) would otherwise pin an entry forever.
_MAX_INFLIGHT = 512
_inflight: "dict[str, float]" = {}


def _bootstrap() -> bool:
    """Make `agent_ledger` importable, or declare the plugin inert."""
    try:
        import agent_ledger  # noqa: F401
        return True
    except ImportError:
        pass

    home = os.environ.get("AGENT_LEDGER_HOME")
    candidates = [Path(home)] if home else []
    # Installed as a symlink into the Hermes plugin tree: follow it back to
    # the checkout, where the library sits two levels up from this file.
    candidates.append(Path(__file__).resolve().parents[2])
    for candidate in candidates:
        if (candidate / "agent_ledger" / "__init__.py").exists():
            sys.path.insert(0, str(candidate))
            try:
                import agent_ledger  # noqa: F401
                return True
            except ImportError:
                continue
    logger.warning(
        "agent-ledger: library not importable; plugin is inert. "
        "Set AGENT_LEDGER_HOME to the checkout directory."
    )
    return False


_READY = _bootstrap()
_store = None


def _get_store():
    global _store
    if not _READY:
        return None
    if _store is None:
        try:
            from agent_ledger.store import Store
            _store = Store()
        except Exception:
            logger.warning("agent-ledger: could not open the ledger", exc_info=True)
            return None
    return _store


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def on_post_api_request(
    *,
    model: str = "",
    provider: str = "",
    usage: Any = None,
    api_duration: float = 0.0,
    finish_reason: str = "",
    session_id: str = "",
    turn_id: str = "",
    api_request_id: str = "",
    platform: str = "",
    **_: Any,
) -> None:
    """One metered request to a model finished. Record what it cost."""
    store = _get_store()
    if store is None:
        return
    try:
        from agent_ledger.event import AgentEvent, KIND_LLM_CALL, OUTCOME_ERROR, OUTCOME_OK
        from agent_ledger.pricing import cost_of

        usage = usage if isinstance(usage, dict) else {}
        input_tokens = _int(usage.get("input_tokens"))
        output_tokens = _int(usage.get("output_tokens"))
        cache_read = _int(usage.get("cache_read_tokens"))
        cache_write = _int(usage.get("cache_write_tokens"))

        cost, source = cost_of(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            host=HOST,
        )

        # Hermes' own request id makes the write idempotent: a retried hook
        # cannot bill the same call twice. Without one we fall back to a
        # fresh id and accept that a retry would double count.
        event_id = f"{HOST}:{api_request_id}" if api_request_id else uuid.uuid4().hex

        store.record(
            AgentEvent(
                id=event_id,
                host=HOST,
                kind=KIND_LLM_CALL,
                agent=platform or "gateway",
                session=session_id,
                turn=turn_id,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                reasoning_tokens=_int(usage.get("reasoning_tokens")),
                cost_usd=cost,
                cost_source=source,
                outcome=OUTCOME_ERROR if finish_reason == "error" else OUTCOME_OK,
                duration_ms=int(max(0.0, api_duration) * 1000),
                detail={"finish_reason": finish_reason} if finish_reason else {},
            )
        )
    except Exception:
        logger.warning("agent-ledger: post_api_request hook failed", exc_info=True)


def on_pre_tool_call(*, tool_call_id: str = "", **_: Any) -> None:
    """Start the clock so the post hook can report a real duration."""
    if not tool_call_id or not _READY:
        return
    if len(_inflight) >= _MAX_INFLIGHT:
        _inflight.clear()
    _inflight[tool_call_id] = time.time()


def on_post_tool_call(
    *,
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    session_id: str = "",
    turn_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> None:
    """A tool finished. Record what it touched, never what it returned."""
    store = _get_store()
    if store is None:
        return
    try:
        from agent_ledger.event import AgentEvent, KIND_TOOL_CALL, OUTCOME_ERROR, OUTCOME_OK

        started = _inflight.pop(tool_call_id, None)
        duration_ms = int((time.time() - started) * 1000) if started else 0

        # The target is the one argument worth keeping: it is what makes an
        # audit line answer "what did it touch". Tool output is deliberately
        # not stored; this is a ledger, not a transcript.
        target = ""
        if isinstance(args, dict):
            for key in ("path", "file_path", "url", "command", "target", "query"):
                value = args.get(key)
                if isinstance(value, str) and value:
                    target = value[:500]
                    break

        failed = isinstance(result, dict) and bool(result.get("error"))

        store.record(
            AgentEvent(
                host=HOST,
                kind=KIND_TOOL_CALL,
                session=session_id,
                turn=turn_id,
                tool=tool_name,
                target=target,
                outcome=OUTCOME_ERROR if failed else OUTCOME_OK,
                duration_ms=duration_ms,
            )
        )
    except Exception:
        logger.warning("agent-ledger: post_tool_call hook failed", exc_info=True)


def _record_plugin_loaded() -> None:
    """Leave one note a day saying the plugin loaded, so silence in the
    ledger becomes visible instead of ambiguous.

    Four Hermes events with none since is exactly what "the plugin broke"
    and "the host just had no turns" both look like from the ledger alone.
    A daily `plugin_loaded` note tells them apart: if it keeps landing and
    `llm_call`/`tool_call` rows do not, Hermes is idle; if it stops landing,
    the plugin (or its host) stopped running. One row per day, via the same
    id every time register() is called that day, so a restart never spams
    the ledger.
    """
    store = _get_store()
    if store is None:
        return
    try:
        from agent_ledger.event import AgentEvent, KIND_NOTE, OUTCOME_OK

        day = time.strftime("%Y-%m-%d", time.gmtime())
        store.record(
            AgentEvent(
                id=f"{HOST}:plugin_loaded:{day}",
                host=HOST,
                kind=KIND_NOTE,
                agent="plugin",
                outcome=OUTCOME_OK,
                detail={"note": "plugin_loaded"},
            )
        )
    except Exception:
        logger.warning("agent-ledger: plugin_loaded self-check failed", exc_info=True)


def register(ctx) -> None:
    """Entry point Hermes calls when the plugin is enabled."""
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    _record_plugin_loaded()
