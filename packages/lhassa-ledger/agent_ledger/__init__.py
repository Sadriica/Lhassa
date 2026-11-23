# SPDX-License-Identifier: AGPL-3.0-or-later
"""agent-ledger: one auditable record of what every agent did.

The pieces:

- `event`   the canonical record every host is normalized into
- `policy`  the never-keep rules, applied inside `Store.record`
- `store`   append-only SQLite, safe to write from inside a host process
- `pricing` tokens to money, with the source of every figure attached
- `query`   the aggregates, each reporting its own blind spot

Adapters live in `adapters/` and depend on this package, never the reverse.
"""

from .event import (
    AgentEvent,
    KIND_LLM_CALL,
    KIND_TOOL_CALL,
    KIND_DECISION,
    KIND_NOTE,
    pending_event,
    resolution_event,
)
from .policy import Policy
from .store import Store

__version__ = "0.8.0"
__all__ = [
    "AgentEvent",
    "Store",
    "Policy",
    "pending_event",
    "resolution_event",
    "KIND_LLM_CALL",
    "KIND_TOOL_CALL",
    "KIND_DECISION",
    "KIND_NOTE",
    "__version__",
]
