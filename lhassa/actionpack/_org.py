# SPDX-License-Identifier: AGPL-3.0-or-later
"""org_run; the fixed action a real delegation resolves to (Etapa 3).

A judgment's ``delegate`` key (``core/dispatch.py``) and a monitor's
``delegations`` (``org/watcher.py``) both mint a task with ``accion="org_run"``
rather than reaching into the org layer themselves; core and the monitor stay
shell-agnostic (core must never import a shell; see ``tests/test_architecture.py``).
This module is the OTHER end of that seam: a shell-layer action (actionpack is a
shell, allowed to depend on org) that lazily imports ``lhassa.org.coordinator``
and actually runs the goal through the real hierarchy at the NEXT tick.

Deterministic and $0 unless ``LHASSA_ORG_LIVE`` is set; the org layer's existing
gate (``org.executor.brain_executor`` / ``org.coordinator``'s synthesis path) does
all the cost-control; this action only wires the plumbing.
"""

from __future__ import annotations


async def org_run(params: dict) -> str:
    """Run a goal through the org hierarchy (``org.coordinator.run_goal``).

    params:
        goal          (required); the natural-language goal to route + execute.
        department              ; optional department hint (routing still
                                    applies on top of the topology; carried
                                    through so callers/tests can see the intent).
        multi                   ; bool; force a fan-out across the top
                                    departments even without a scoring tie
                                    (see ``org.coordinator.run_goal``).
        high_risk               ; bool; parks a multi-person approval inside
                                    the org layer. With no distinct ``approver``
                                    supplied here, the leaf correctly refuses to
                                    self-approve (``SelfApprovalError``); caught
                                    below and reported, never raised.
        by_actor                ; actor id recorded as the proposer (default
                                    "org_run").
        instance_id              ; defaults to Config.instance_id.

    Never raises; any failure (including a refused self-approval) degrades to
    an explanatory string, matching every other action in this package.
    """
    try:
        if not isinstance(params, dict):
            params = {}
        goal = str(params.get("goal") or "").strip()
        if not goal:
            return "org_run: 'goal' param is required"

        from lhassa.config import Config
        from lhassa.org.coordinator import run_goal

        instance_id = params.get("instance_id") or Config.instance_id
        by_actor = str(params.get("by_actor") or "org_run")
        high_risk = bool(params.get("high_risk"))
        run_params = {}
        if params.get("multi"):
            run_params["multi"] = True
        if params.get("department"):
            run_params["department"] = str(params.get("department"))

        record = await run_goal(
            goal,
            by_actor=by_actor,
            high_risk=high_risk,
            instance_id=instance_id,
            params=run_params,
        )
        dept = record.get("department") or record.get("leaf")
        result = record.get("result")
        multi_note = " (multi)" if record.get("multi") else ""
        return f"org_run: routed to {dept}{multi_note}; {str(result)[:300]}"

    except Exception as exc:
        # A refused self-approval (high-risk, no distinct approver) lands here
        # too; an action must never raise; the outcome is reported as text.
        return f"org_run: {type(exc).__name__}: {exc}"
