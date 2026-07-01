"""The organizational layer (Fase 7) — a coordinated two-brain core.

This package is the foundation/skeleton of Lhassa's organizational layer: a
**general** brain that *coordinates and routes* work (it never executes the
department's work itself) and **department** brains that execute with their own
identity. Every hierarchy concept appears here in its minimal form:

  - ``messaging`` — TYPED inter-brain messages (a closed kind set, never free
    prose); the task queue between brains.
  - ``commons``   — the ONE read-only cross-cutting source of truth; the general
    is its sole curator, every brain reads it, no per-department copies.
  - ``rbac``      — an explicit, table-driven role→permission matrix.
  - ``coordinator`` — the two-brain core: general ``assign``/``collect``/
    ``assign_auto`` and department ``handle`` (with an injectable executor that
    defaults to the real department brain) plus the multi-person approval hook
    (proposer ≠ approver for high-risk work).
  - ``executor``  — the real department executor (:func:`brain_executor`): a
    department's one-turn think path (identity + memory + routine provider), offline
    by default so it stays test-safe.
  - ``routing``   — a deterministic, table-driven department registry + ``route``
    (keyword match, NO LLM) that the general uses to pick a department.

Golden rules (from the brief, §3-Fase 7): the general coordinates and does NOT
execute; communication is typed messages; one read-only commons (no diverging
memories); start with TWO brains (one general + one department), never six.
"""

from lhassa.org import commons, delegate, executor, messaging, monitor, rbac, routing, topology
from lhassa.org.coordinator import (
    DepartmentBrain,
    GeneralBrain,
    route_in_topology,
    run_goal,
)
from lhassa.org.delegate import delegate_flow, owning_department
from lhassa.org.executor import brain_executor
from lhassa.org.messaging import Message, MessageKind, UnknownMessageKind
from lhassa.org.monitor import (
    gather_signals,
    make_brain_monitor,
    monitor_pass,
)
from lhassa.org.rbac import can, require
from lhassa.org.routing import (
    Department,
    list_departments,
    register,
    route,
)
from lhassa.org.topology import (
    BrainNode,
    Topology,
    TopologyError,
    default_topology,
    load_topology,
    save_topology,
)

__all__ = [
    "commons",
    "delegate",
    "delegate_flow",
    "owning_department",
    "executor",
    "messaging",
    "monitor",
    "rbac",
    "routing",
    "topology",
    "gather_signals",
    "monitor_pass",
    "make_brain_monitor",
    "Message",
    "MessageKind",
    "UnknownMessageKind",
    "GeneralBrain",
    "DepartmentBrain",
    "run_goal",
    "route_in_topology",
    "brain_executor",
    "Department",
    "route",
    "register",
    "list_departments",
    "can",
    "require",
    "BrainNode",
    "Topology",
    "TopologyError",
    "default_topology",
    "load_topology",
    "save_topology",
]
