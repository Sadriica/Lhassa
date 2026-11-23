# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Shared machinery for the voice-tool handlers.

Everything here is used across more than one handler family, so it lives in a
single leaf module the family modules import; no handler family imports another
handler family. Family-specific helpers (e.g. the Microsoft Graph / GitHub token
lookups) stay in their own family module.
"""

from contextvars import ContextVar

from lhassa.state import state


def _bump_tool():
    """Increment the tool_calls metric."""
    state.metrics["tool_calls"] += 1


# Which DOOR is driving the tool call right now. The handler families are shared
# (voice, CLI, agents all run the same code), so the audit trail has to be told,
# not guessed: without this every CLI shell command was filed as "voice_tool".
# The default keeps the historical value, so nothing that does not set it changes.
_DOOR: ContextVar[str] = ContextVar("lhassa_tool_door", default="voice_tool")


def current_door() -> str:
    """The audit ``source`` for the tool call being handled right now."""
    return _DOOR.get()


def set_door(name: str):
    """Bind the door for this context; returns the token to reset with."""
    return _DOOR.set(name or "voice_tool")


def reset_door(token) -> None:
    """Undo a :func:`set_door`, ignoring a token from another context."""
    try:
        _DOOR.reset(token)
    except ValueError:  # set in a different context; nothing to undo here
        pass
