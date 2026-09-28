# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic department routing; how the general picks a department (Fase 7).

The general COORDINATES: given a goal, it must choose which department gets the
typed ``TASK_ASSIGN``. That choice is **deterministic and table-driven. NO LLM**.
Each :class:`Department` declares the capability keywords/domains it owns; routing
scores a goal against those keyword tables and picks the best match, falling back
to a documented default department when nothing matches. The whole policy is a
lookup, so it is transparent, free, and testable; the same spirit as
:mod:`lhassa.org.rbac` (a closed table, auditable at a glance).

The registry starts with at least two departments (``optimization`` and
``documentation``, plus ``finance``) and is open for ``register``. ``route`` never
calls a model; the general's :meth:`GeneralBrain.assign_auto` uses it to choose the
target and then assigns as usual; the general routes, it never executes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Department:
    """A routable department: a name plus the capability keywords it owns.

    ``keywords`` are matched case-insensitively on whole-word boundaries against a
    goal. ``description`` is a short human label for demos/inspection.
    """

    name: str
    keywords: frozenset[str] = field(default_factory=frozenset)
    description: str = ""


# The fallback department used when a goal matches no keyword table. ``optimization``
# is Lhassa's first specialist domain (Phase 5), so it is the natural default home
# for unclassified work.
FALLBACK_DEPARTMENT = "optimization"


# ── the registry (insertion-ordered; first-registered wins ties) ──────────────────

_REGISTRY: dict[str, Department] = {}


def register(dept: Department) -> Department:
    """Register (or replace) a department in the routing registry. Returns it."""
    _REGISTRY[dept.name] = dept
    return dept


def list_departments() -> list[Department]:
    """All registered departments, in registration order."""
    return list(_REGISTRY.values())


def get_department(name: str) -> Department | None:
    """The registered :class:`Department` for ``name`` (or ``None``)."""
    return _REGISTRY.get(name)


# ── the default departments (at least two, per the brief) ─────────────────────────

register(
    Department(
        name="optimization",
        description="performance, latency, and efficiency work",
        keywords=frozenset(
            {
                "optimize", "optimise", "optimization", "optimisation", "optimizar",
                "performance", "perf", "latency", "speed", "slow", "slower", "faster",
                "fast", "profile", "profiling", "benchmark", "throughput",
                "efficiency", "efficient", "bottleneck", "cpu", "memory", "cache",
                "tune", "tuning", "accelerate", "rendimiento", "lento", "velocidad",
            }
        ),
    )
)

register(
    Department(
        name="documentation",
        description="documentation, write-ups, and explanations",
        keywords=frozenset(
            {
                "document", "documentation", "documenting", "docs", "doc",
                "documentar", "readme", "manual", "guide", "tutorial", "write-up",
                "writeup", "explain", "explanation", "describe", "comment",
                "comments", "changelog", "notes", "spec", "redactar", "redaccion",
                "escribir",
            }
        ),
    )
)

register(
    Department(
        name="finance",
        description="budgets, payments, and accounting",
        keywords=frozenset(
            {
                "finance", "financial", "budget", "budgeting",
                "presupuesto", "invoice", "factura", "payment", "pay", "pago",
                "funds", "fund", "money", "dinero", "cost", "costs", "expense",
                "expenses", "gasto", "gastos", "accounting", "ledger", "reconcile",
                "reconciliation", "revenue", "tax", "impuesto",
            }
        ),
    )
)


# ── the deterministic router ──────────────────────────────────────────────────────


def _score(goal_lower: str, dept: Department) -> int:
    """How many distinct keywords of ``dept`` appear (whole-word) in ``goal_lower``."""
    score = 0
    for kw in dept.keywords:
        if re.search(rf"\b{re.escape(kw)}\b", goal_lower):
            score += 1
    return score


def route(goal: str) -> str:
    """Pick the best department for ``goal`` by keyword match; deterministic, no LLM.

    Scores the goal against each registered department's keyword table and returns
    the highest-scoring department's name. Ties break by registration order (the
    first-registered department wins). A goal that matches nothing falls back to
    :data:`FALLBACK_DEPARTMENT`. Pure and free; never calls a model.
    """
    goal_lower = (goal or "").lower()
    best_name = FALLBACK_DEPARTMENT
    best_score = 0
    for dept in _REGISTRY.values():
        score = _score(goal_lower, dept)
        if score > best_score:
            best_score = score
            best_name = dept.name
    return best_name
