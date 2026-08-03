# SPDX-License-Identifier: AGPL-3.0-or-later
"""The work-flow graph model: nodes, edges, a flow, validation, JSON round-trip.

A flow is a directed acyclic graph. Nodes are one of a CLOSED set of kinds
(:class:`NodeKind`); edges wire one node to another and may carry a ``when``
condition (used by ``branch`` nodes and to gate a path on a node's outcome).

Validation (:meth:`Flow.validate`) enforces the structural rules the engine relies
on, and raises :class:`FlowValidationError` on the first problem:

  * node ids are present and unique,
  * exactly one ``trigger`` node (a flow has a single, unambiguous entry point),
  * every edge references real, distinct nodes and nothing points back INTO the
    trigger,
  * the graph is acyclic — loops are NOT supported (a DAG executes in one pass);
    this is a deliberate simplification documented for the execution engine.

``to_dict``/``from_dict`` round-trip losslessly (the value of :class:`NodeKind`
serialises to its plain string), so the store and the API can persist and rebuild a
flow without surprises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FlowValidationError(ValueError):
    """A flow that violates a structural rule (bad ids/edges/trigger/cycle)."""


class NodeKind(str, Enum):
    """The CLOSED set of node kinds a work-flow may contain.

    A ``str`` enum so a kind serialises to its plain value (``"task"``) and compares
    equal to that string — JSON-friendly and ergonomic.
    """

    TRIGGER = "trigger"
    TASK = "task"
    GATE = "gate"
    OUTPUT = "output"
    BRANCH = "branch"

    @classmethod
    def coerce(cls, value: Any) -> "NodeKind":
        """Coerce a string/enum into a :class:`NodeKind`, else raise.

        Raises :class:`FlowValidationError` (not a bare ``ValueError``) so a bad
        kind surfaces as a flow-validation problem the API can turn into a 4xx.
        """
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            allowed = ", ".join(k.value for k in cls)
            raise FlowValidationError(
                f"unknown node kind {value!r} (allowed: {allowed})"
            ) from exc


@dataclass
class FlowNode:
    """One node in the graph.

    ``config`` is a free-form dict whose shape depends on ``kind``:

      * ``trigger`` → ``{"type": "manual"|"schedule"|"event", ...}``
      * ``task``    → HYBRID routing — ``{"skill"?: str, "brain"?: str,
        "prompt"?: str}``; ``brain`` PINS a specific brain, otherwise ``skill``
        (or the prompt text) auto-routes through the org topology.
      * ``gate``    → ``{"type": "verify"|"approval", "spec": {...}}``
      * ``output``  → ``{"type": "notify"|"doc"|"log", "target"?: str}``
      * ``branch``  → ``{}`` (the condition lives on the outgoing edges' ``when``).
    """

    id: str
    kind: NodeKind
    config: dict = field(default_factory=dict)
    label: str = ""

    def __post_init__(self) -> None:
        self.kind = NodeKind.coerce(self.kind)
        if self.config is None:
            self.config = {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "config": dict(self.config or {}),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FlowNode":
        if not isinstance(data, dict):
            raise FlowValidationError(f"node must be an object, got {type(data).__name__}")
        return cls(
            id=str(data.get("id", "")),
            kind=NodeKind.coerce(data.get("kind")),
            config=dict(data.get("config") or {}),
            label=str(data.get("label") or ""),
        )


@dataclass
class FlowEdge:
    """A directed wire ``from_node`` → ``to_node``.

    ``when`` is an optional condition (see :func:`lhassa.flows.engine.eval_when`).
    An edge with ``when=None`` is unconditional. For a ``branch`` node the engine
    picks the FIRST outgoing edge whose ``when`` matches (an unconditional edge acts
    as the ``else``). For a ``gate`` node a ``when`` lets a path branch on the
    pass/fail verdict.
    """

    from_node: str
    to_node: str
    when: Optional[dict] = None

    def to_dict(self) -> dict:
        out = {"from_node": self.from_node, "to_node": self.to_node}
        if self.when is not None:
            out["when"] = dict(self.when)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "FlowEdge":
        if not isinstance(data, dict):
            raise FlowValidationError(f"edge must be an object, got {type(data).__name__}")
        # Accept both the canonical names and the loose "from"/"to" the canvas may send.
        frm = data.get("from_node", data.get("from"))
        to = data.get("to_node", data.get("to"))
        when = data.get("when")
        if when is not None and not isinstance(when, dict):
            raise FlowValidationError("edge 'when' must be an object or omitted")
        return cls(from_node=str(frm or ""), to_node=str(to or ""), when=when)


@dataclass
class Flow:
    """A named work-flow: its nodes and the edges that wire them."""

    id: str
    name: str
    description: str = ""
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)

    # ── lookups ───────────────────────────────────────────────────────────────
    def node(self, node_id: str) -> FlowNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def trigger(self) -> FlowNode:
        """The single trigger node (assumes a validated flow)."""
        for n in self.nodes:
            if n.kind == NodeKind.TRIGGER:
                return n
        raise FlowValidationError("flow has no trigger node")

    def edges_from(self, node_id: str) -> list[FlowEdge]:
        return [e for e in self.edges if e.from_node == node_id]

    # ── validation ──────────────────────────────────────────────────────────--
    def validate(self) -> "Flow":
        """Enforce the structural rules; raise :class:`FlowValidationError` on any.

        Returns ``self`` so callers can write ``flow = Flow(...).validate()``.
        """
        if not (self.id or "").strip():
            raise FlowValidationError("flow id is required")
        if not (self.name or "").strip():
            raise FlowValidationError("flow name is required")

        # Unique, non-empty node ids.
        seen: set[str] = set()
        for n in self.nodes:
            if not (n.id or "").strip():
                raise FlowValidationError("every node needs a non-empty id")
            if n.id in seen:
                raise FlowValidationError(f"duplicate node id {n.id!r}")
            seen.add(n.id)

        # Exactly one trigger.
        triggers = [n for n in self.nodes if n.kind == NodeKind.TRIGGER]
        if len(triggers) != 1:
            raise FlowValidationError(
                f"a flow needs exactly one trigger node, found {len(triggers)}"
            )
        trigger_id = triggers[0].id

        # Edges reference real, distinct nodes; nothing points back into the trigger.
        for e in self.edges:
            if e.from_node not in seen:
                raise FlowValidationError(f"edge from unknown node {e.from_node!r}")
            if e.to_node not in seen:
                raise FlowValidationError(f"edge to unknown node {e.to_node!r}")
            if e.from_node == e.to_node:
                raise FlowValidationError(f"self-loop edge on node {e.from_node!r}")
            if e.to_node == trigger_id:
                raise FlowValidationError("no edge may point back into the trigger")

        self._assert_acyclic()
        return self

    def _assert_acyclic(self) -> None:
        """DFS cycle check — the engine executes a DAG in a single pass."""
        adj: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for e in self.edges:
            adj[e.from_node].append(e.to_node)
        WHITE, GREY, BLACK = 0, 1, 2
        color = {n.id: WHITE for n in self.nodes}

        def visit(node_id: str, stack: list[str]) -> None:
            color[node_id] = GREY
            for nxt in adj[node_id]:
                if color[nxt] == GREY:
                    cycle = " → ".join([*stack, node_id, nxt])
                    raise FlowValidationError(f"flow has a cycle: {cycle}")
                if color[nxt] == WHITE:
                    visit(nxt, [*stack, node_id])
            color[node_id] = BLACK

        for n in self.nodes:
            if color[n.id] == WHITE:
                visit(n.id, [])

    # ── serialisation ─────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Flow":
        if not isinstance(data, dict):
            raise FlowValidationError(f"flow must be an object, got {type(data).__name__}")
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            nodes=[FlowNode.from_dict(d) for d in (data.get("nodes") or [])],
            edges=[FlowEdge.from_dict(d) for d in (data.get("edges") or [])],
        )
