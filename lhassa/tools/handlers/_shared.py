"""
Shared machinery for the voice-tool handlers.

Everything here is used across more than one handler family, so it lives in a
single leaf module the family modules import — no handler family imports another
handler family. Family-specific helpers (e.g. the Microsoft Graph / GitHub token
lookups) stay in their own family module.
"""

from lhassa.state import state


def _bump_tool():
    """Increment the tool_calls metric."""
    state.metrics["tool_calls"] += 1
