# SPDX-License-Identifier: AGPL-3.0-or-later
"""The canonical record.

One event is one thing an agent did that costs money, touches the outside
world, or needs a human's consent. Every host (Hermes, Claude Code, Orca,
OMP) is normalized into this shape before it is written, so a single query
can answer "what did my agents spend today" across all of them.

Design rules, in priority order:

1. Never fabricate. A field the host did not report stays empty, and a
   derived value carries the source that produced it (see `cost_source`).
2. Append only. An event is written once and never updated; corrections are
   new events that reference the original.
3. Cheap to write. Recording an event must not need a model, a network call
   or a lock held across IO.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# What kind of thing happened. Kept small on purpose: a vocabulary that grows
# per host is a vocabulary no cross-host query can use.
KIND_LLM_CALL = "llm_call"      # a metered request to a model
KIND_TOOL_CALL = "tool_call"    # a tool the agent invoked
KIND_DECISION = "decision"      # a gate: allowed, denied, or awaiting a human
KIND_NOTE = "note"              # anything the host wants on the record

KINDS = frozenset({KIND_LLM_CALL, KIND_TOOL_CALL, KIND_DECISION, KIND_NOTE})

# Finer vocabulary, without new kinds: the four kinds above stay the only
# values of `kind`, and a newer event type rides in `detail["type"]`. The
# convention (see the pack README):
#
#   resource_sample   kind=note      a CPU/RAM/GPU/temperature sample
#   file_event        kind=tool_call a file the agent created, changed or read
#   shell_cmd         kind=tool_call a shell command
#   window_focus      kind=note      the focused window changed
#   policy_decision   kind=decision  a gate: allowed, denied or pending
#   limit             kind=decision  a budget or rate limit was reached
DETAIL_TYPE_RESOURCE_SAMPLE = "resource_sample"
DETAIL_TYPE_FILE_EVENT = "file_event"
DETAIL_TYPE_SHELL_CMD = "shell_cmd"
DETAIL_TYPE_WINDOW_FOCUS = "window_focus"
DETAIL_TYPE_POLICY_DECISION = "policy_decision"
DETAIL_TYPE_LIMIT = "limit"

DETAIL_TYPES = frozenset({
    DETAIL_TYPE_RESOURCE_SAMPLE,
    DETAIL_TYPE_FILE_EVENT,
    DETAIL_TYPE_SHELL_CMD,
    DETAIL_TYPE_WINDOW_FOCUS,
    DETAIL_TYPE_POLICY_DECISION,
    DETAIL_TYPE_LIMIT,
})

# How an event resolved. `pending` is the one that matters for governance:
# it is an action waiting on a human, and it is the state a dashboard must
# never let go quiet.
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_DENIED = "denied"
OUTCOME_PENDING = "pending"

# Where a cost figure came from. An unlabeled number is a number nobody can
# audit, so cost and its provenance always travel together.
COST_FROM_HOST = "host"        # the host reported the charge itself
COST_FROM_TABLE = "table"      # computed here from tokens and a price entry
COST_UNKNOWN = "unknown"       # tokens known, price not; cost stays None


@dataclass(frozen=True)
class AgentEvent:
    """One append-only row. Construct it, hand it to a Store, forget it."""

    host: str                              # "hermes", "claude-code", "orca", "omp"
    kind: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)

    # Who did it. `agent` is the host's own name for the actor; `session` and
    # `turn` let a reader reassemble a conversation without the host's DB.
    agent: str = ""
    session: str = ""
    turn: str = ""

    # Where it ran (schema v2). `machine_id` names the computer, `instance_id`
    # one running copy of the host on it. Both stay empty when unknown.
    instance_id: str = ""
    machine_id: str = ""

    # What it cost. Tokens are integers the host reported; cost_usd is only
    # set when `cost_source` says where it came from.
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: Optional[float] = None
    cost_source: str = COST_UNKNOWN

    # What it touched.
    tool: str = ""
    target: str = ""                       # file, url, host: whatever the tool acted on
    outcome: str = OUTCOME_OK
    duration_ms: int = 0

    # Free-form host detail. Kept out of the columns above so a new host can
    # carry its own vocabulary without a migration.
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown kind {self.kind!r}; expected one of {sorted(KINDS)}")
        if not self.host:
            raise ValueError("every event must name its host")
        if self.cost_usd is not None and self.cost_source == COST_UNKNOWN:
            raise ValueError("a cost without a source cannot be audited; set cost_source")

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def pending_event(
    host: str,
    tool: str = "",
    target: str = "",
    *,
    agent: str = "",
    session: str = "",
    reason: str = "",
    **fields: Any,
) -> AgentEvent:
    """An action waiting on a human: `kind=decision`, `outcome=pending`.

    `ledger pending` lists these. The ledger is append-only, so answering one
    is a second event built with `resolution_event`, not an update.
    """
    detail = dict(fields.pop("detail", {}) or {})
    detail.setdefault("type", DETAIL_TYPE_POLICY_DECISION)
    if reason:
        detail.setdefault("reason", reason)
    return AgentEvent(
        host=host,
        kind=KIND_DECISION,
        tool=tool,
        target=target,
        agent=agent,
        session=session,
        outcome=OUTCOME_PENDING,
        detail=detail,
        **fields,
    )


def resolution_event(pending: "AgentEvent | str", outcome: str, **fields: Any) -> AgentEvent:
    """The answer to a pending event: a new decision that names the original.

    `pending` is the pending event or its id. `outcome` is how it resolved
    (`ok` for approved, `denied`, `error`). Once recorded, `ledger pending`
    stops listing the original.
    """
    if outcome == OUTCOME_PENDING:
        raise ValueError("a resolution cannot itself be pending")
    original = pending if isinstance(pending, str) else pending.id
    host = fields.pop("host", None) or (pending.host if not isinstance(pending, str) else "")
    detail = dict(fields.pop("detail", {}) or {})
    detail.setdefault("type", DETAIL_TYPE_POLICY_DECISION)
    detail["resolves"] = original
    return AgentEvent(host=host, kind=KIND_DECISION, outcome=outcome, detail=detail, **fields)
