"""
Axis 2 — the ``HarnessAdapter`` seam (§2.2): delegated agentic *code* work.

The brain never calls a concrete coding CLI; it calls a ``HarnessAdapter``. Three
muscles are supported — ``opencode``, Claude Code (``claude``) and ``codex`` — each
with different capabilities (Claude Code locked to Claude, Codex to OpenAI,
opencode flexible). ``runner`` launches one as a separate process (PID/stream) and
supervises it asynchronously via disk, so ``step()`` never blocks ("lanza y duerme").
"""

from lhassa.harness.adapter import (
    ClaudeCodeAdapter,
    CodexAdapter,
    HarnessAdapter,
    OpenCodeAdapter,
    RunEvent,
    available_adapters,
    get_adapter,
)

__all__ = [
    "RunEvent",
    "HarnessAdapter",
    "OpenCodeAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "get_adapter",
    "available_adapters",
]
