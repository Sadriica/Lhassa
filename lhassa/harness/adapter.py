"""
``HarnessAdapter`` — the Axis-2 interface (§2.2): delegated agentic code work.

Each adapter is a thin, *trivially editable* description of one coding CLI:
  • ``build_command(prompt, **opts)`` — the argv to run it non-interactively.
  • ``parse_line(raw)`` — map one stdout line to a ``RunEvent`` (the prototype is
    already built — ``stream-json`` parsing; a plain line degrades to a text event).
  • ``available()`` — is the CLI installed (``shutil.which``)?

Plus three static facts the router reasons over: ``name``, ``model_locked`` (is the
muscle tied to one model family?) and ``billing`` ("metered" | "subscription" |
"free"). Keep the per-CLI argv/flags below easy to edit; flag assumptions that have
not been smoke-tested are marked ``[SUPUESTO]``.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

# Strip ANSI escape codes the CLIs may emit before parsing a line.
_ANSI = re.compile(r"\x1b\[[0-9;]*[mGKHF]")


@dataclass
class RunEvent:
    """One parsed unit of harness output.

    ``kind``     — "text" | "tool" | "cost" | "done" | "error".
    ``payload``  — the human-readable text for the event (may be empty).
    ``cost_usd`` — set only when the line reports a cost (``stream-json`` result).
    """

    kind: str
    payload: str = ""
    cost_usd: Optional[float] = None


@runtime_checkable
class HarnessAdapter(Protocol):
    """The pluggable coding muscle (§2.2). Implementations stay tiny by design."""

    name: str
    model_locked: bool
    billing: str  # "metered" | "subscription" | "free"

    def build_command(self, prompt: str, **opts) -> list[str]:
        """Return the full argv that runs this CLI non-interactively on ``prompt``."""
        ...

    def parse_line(self, raw: str) -> Optional[RunEvent]:
        """Map one stdout line to a ``RunEvent`` (or ``None`` for a blank line)."""
        ...

    def available(self) -> bool:
        """True iff the CLI executable is found in PATH."""
        ...


# ── shared stream-json / plain-line parsing ──────────────────────────────────────

def _try_json(raw: str) -> Optional[dict]:
    """Parse ``raw`` as a JSON object, or ``None`` if it is not one."""
    raw = raw.strip()
    if not raw or raw[0] not in "{[":
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _extract_text(obj: dict) -> str:
    """Pull the assistant text out of a stream-json object, best-effort."""
    if isinstance(obj.get("text"), str):
        return obj["text"]
    msg = obj.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, list):
            parts = [
                blk.get("text", "")
                for blk in content
                if isinstance(blk, dict) and blk.get("type") == "text"
            ]
            joined = "".join(parts).strip()
            if joined:
                return joined
        if isinstance(content, str):
            return content
    if isinstance(obj.get("content"), str):
        return obj["content"]
    return ""


def _event_from_json(obj: dict, raw: str) -> RunEvent:
    """Map a parsed stream-json object to a RunEvent."""
    kind = obj.get("type")
    cost = obj.get("total_cost_usd", obj.get("cost_usd"))

    if kind == "result" or cost is not None:
        try:
            cost_val = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost_val = None
        return RunEvent(kind="cost", payload=str(obj.get("subtype", "result")),
                        cost_usd=cost_val)
    if kind == "error" or obj.get("is_error"):
        msg = obj.get("error") or obj.get("message") or raw.strip()
        return RunEvent(kind="error", payload=str(msg))
    if kind in ("tool_use", "tool", "tool_result"):
        return RunEvent(kind="tool", payload=str(obj.get("name", obj.get("type", ""))))
    return RunEvent(kind="text", payload=_extract_text(obj) or raw.strip())


class _BaseAdapter:
    """Shared parsing + availability. Subclasses only supply argv + static facts."""

    name: str = ""
    model_locked: bool = False
    billing: str = "metered"
    binary: str = ""

    def available(self) -> bool:
        return bool(self.binary) and shutil.which(self.binary) is not None

    def parse_line(self, raw: str) -> Optional[RunEvent]:
        clean = _ANSI.sub("", raw).rstrip("\n")
        if not clean.strip():
            return None
        obj = _try_json(clean)
        if obj is not None:
            return _event_from_json(obj, clean)
        return RunEvent(kind="text", payload=clean.strip())

    def build_command(self, prompt: str, **opts) -> list[str]:  # pragma: no cover
        raise NotImplementedError


# ── the three adapters (per-CLI argv table — edit here) ──────────────────────────

class OpenCodeAdapter(_BaseAdapter):
    """opencode — flexible (any model); pay-as-you-go via opencode Zen credits."""

    name = "opencode"
    model_locked = False
    billing = "metered"
    binary = "opencode"

    def build_command(self, prompt: str, **opts) -> list[str]:
        # [SUPUESTO] `opencode run <prompt>` runs non-interactively with the prompt
        # as a single positional arg. If a build emits stream-json, parse_line
        # already handles it; otherwise plain lines degrade to text events.
        return ["opencode", "run", prompt]


class ClaudeCodeAdapter(_BaseAdapter):
    """Claude Code — locked to Claude; billed against the Claude subscription."""

    name = "claude"
    model_locked = True
    billing = "subscription"
    binary = "claude"

    def build_command(self, prompt: str, *, stream_json: bool = True, **opts) -> list[str]:
        if stream_json:
            # [SUPUESTO] `--output-format stream-json` requires `--verbose` under
            # `--print`. Structured events let the runner accumulate live cost.
            return ["claude", "--print", "--output-format", "stream-json",
                    "--verbose", prompt]
        return ["claude", "--print", prompt]


class CodexAdapter(_BaseAdapter):
    """Codex — locked to OpenAI; billed against the Codex/OpenAI subscription."""

    name = "codex"
    model_locked = True
    billing = "subscription"
    binary = "codex"

    def build_command(self, prompt: str, **opts) -> list[str]:
        # [SUPUESTO] `codex exec <prompt>` is the non-interactive subcommand; it
        # streams plain lines (parse_line still handles JSON if a build emits it).
        return ["codex", "exec", prompt]


# ── registry / detection ─────────────────────────────────────────────────────────

# Insertion order is the preference order when billing rank ties (opencode first).
_ADAPTERS: dict[str, HarnessAdapter] = {
    a.name: a for a in (OpenCodeAdapter(), ClaudeCodeAdapter(), CodexAdapter())
}


def get_adapter(name: str) -> HarnessAdapter:
    """Return the adapter registered under ``name`` (case-insensitive)."""
    try:
        return _ADAPTERS[(name or "").lower()]
    except KeyError:
        raise ValueError(
            f"Unknown harness {name!r}. Valid options: {', '.join(_ADAPTERS)}."
        )


def available_adapters() -> list[HarnessAdapter]:
    """Return the installed adapters, in registry (preference) order."""
    return [a for a in _ADAPTERS.values() if a.available()]


def all_adapters() -> list[HarnessAdapter]:
    """Return every known adapter regardless of availability."""
    return list(_ADAPTERS.values())
