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
  - ``coordinator`` — the two-brain core: general ``assign``/``collect`` and
    department ``handle`` (with an injectable, offline executor) plus the
    multi-person approval hook (proposer ≠ approver for high-risk work).

Golden rules (from the brief, §3-Fase 7): the general coordinates and does NOT
execute; communication is typed messages; one read-only commons (no diverging
memories); start with TWO brains (one general + one department), never six.
"""

from lhassa.org import commons, messaging, rbac
from lhassa.org.coordinator import DepartmentBrain, GeneralBrain
from lhassa.org.messaging import Message, MessageKind, UnknownMessageKind
from lhassa.org.rbac import can, require

__all__ = [
    "commons",
    "messaging",
    "rbac",
    "Message",
    "MessageKind",
    "UnknownMessageKind",
    "GeneralBrain",
    "DepartmentBrain",
    "can",
    "require",
]
