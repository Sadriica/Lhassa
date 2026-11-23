# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The versatile brain. Plan 4, Stream 1's composition seam.

Wires the three deterministic, $0 axes together for one inbound message:

  1. **task identification**: ``notify.intent.classify_work_type`` (regex,
     what kind of work is this).
  2. **working mode**       : ``notify.modes.resolve_mode`` (registry, how
     should Lhassa go about it).
  3. **effort level**       : ``core.effort.resolve_effort`` (table lookup +
     an in-message ``--<level>`` marker, how much budget/loop/verifier/MoA).

``route()`` is the one function a caller (the Telegram chat think today; any
future surface tomorrow) needs to call BEFORE spending anything. It never
raises and never calls a model; every field on :class:`RoutingDecision` is
knowable for $0. :func:`describe` renders it into one line fit for an
Episode's ``decision`` field, so the trajectory a request took (and the cost
it declared before the think ran) is visible in the log, not just inferred
from what happened to run.
"""

from __future__ import annotations

from dataclasses import dataclass

from lhassa.core.effort import EffortProfile, resolve_effort
from lhassa.notify.intent import classify_work_type
from lhassa.notify.modes import WorkingMode, resolve_mode

__all__ = ["RoutingDecision", "route", "describe"]


@dataclass(frozen=True)
class RoutingDecision:
    """The resolved trajectory for one message, BEFORE any spend.

    ``work_type``   : the task-identification axis's verdict.
    ``mode``         : the resolved :class:`~lhassa.notify.modes.WorkingMode`.
    ``effort``       : the resolved :class:`~lhassa.core.effort.EffortProfile`.
    ``text``         : the message with any ``--<level>`` effort marker
                         stripped (what should actually reach the model).
    """

    work_type: str
    mode: WorkingMode
    effort: EffortProfile
    text: str


def route(text: str, *, effort_override: "str | None" = None) -> RoutingDecision:
    """Resolve work type + mode + effort for ``text``. Never raises, $0.

    A malformed/empty ``text`` degrades to the cheapest trajectory: work type
    ``"question"``, mode ``answer_directly``, effort ``minimal`` (via
    ``classify_work_type``'s and ``resolve_effort``'s own safe defaults);
    never a silently expensive guess.
    """
    try:
        raw = text or ""
    except Exception:
        raw = ""

    work_type = classify_work_type(raw)
    mode = resolve_mode(work_type)
    effort, cleaned = resolve_effort(
        work_type=work_type, message=raw, override=effort_override
    )
    return RoutingDecision(work_type=work_type, mode=mode, effort=effort, text=cleaned)


def describe(decision: RoutingDecision) -> str:
    """Render a routing decision into one Episode-ready line.

    Declares the trajectory AND the pre-spend cost estimate; this is what
    makes "minimal vs. deep produced visibly different trajectories, cost
    declared before it was spent" checkable straight from the episode log,
    never only from a test.
    """
    eff = decision.effort
    return (
        f"route: type={decision.work_type} mode={decision.mode.name} "
        f"effort={eff.level} budget={eff.token_budget}tk turns={eff.loop_turns} "
        f"verifiers={eff.verifier_count} moa={eff.moa} "
        f"declared_cost≈${eff.estimated_cost_usd}"
    )
