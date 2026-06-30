"""Work-flows — an n8n-style graph that wires trigger → task → gate → output.

A *work-flow* is a small directed graph (a DAG) whose nodes are typed work and
whose edges carry data and branch conditions:

  - ``trigger``  — how the flow starts (manual | schedule | event).
  - ``task``     — a brain does work. Routing is **HYBRID**: by default the node's
    ``skill``/domain auto-routes through the org topology (reusing the org's
    deterministic routing); a node may instead PIN a specific ``brain``.
  - ``gate``     — an objective checkpoint (``verify``) or a human checkpoint
    (``approval``). On a non-pass the path stops (or a branch reroutes it).
  - ``output``   — record a result (``notify`` | ``doc`` | ``log``); best-effort.
  - ``branch``   — pick one outgoing edge by evaluating a simple ``when`` condition.

This package is a self-contained FOUNDATION:

  - :mod:`lhassa.flows.model`  — the graph data model + validation + JSON round-trip.
  - :mod:`lhassa.flows.store`  — a crash-safe JSON store over ``config.flows_file``.
  - :mod:`lhassa.flows.engine` — a small, offline-testable async executor.

The live canvas (a frontend slice) and live execution sit on top of this; live work
is gated behind ``LHASSA_ORG_LIVE`` exactly like the rest of the codebase, so the
whole foundation is offline-testable and spends nothing by default.
"""

from lhassa.flows.model import (
    Flow,
    FlowEdge,
    FlowNode,
    FlowValidationError,
    NodeKind,
)

__all__ = [
    "Flow",
    "FlowEdge",
    "FlowNode",
    "FlowValidationError",
    "NodeKind",
]
