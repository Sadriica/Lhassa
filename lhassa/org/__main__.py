# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline demo of the organizational layer — ``python -m lhassa.org``.

Wires a general + the registered departments with an INJECTED fake executor (so it
runs with no model and no keys, spending nothing), routes a few sample goals to the
right departments via the deterministic router, has each department handle its task,
and prints the typed message flow + results.

Everything is offline and deterministic: the router is table-driven (no LLM) and the
executor is a fake that returns a structured result without calling a model. Run with
``python -m lhassa.org``.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from lhassa.org import messaging, routing
from lhassa.org.coordinator import DepartmentBrain, GeneralBrain, department_identity

_INSTANCE = "inst_demo"

# A few goals chosen to land in different departments (plus one that falls back).
_GOALS = [
    "optimize the heartbeat latency, the loop feels slow",
    "draft the documentation and a readme for the org module",
    "reconcile the monthly budget and the pending invoice",
    "do the thing nobody named a domain for",  # no keywords → fallback
]


def _fake_executor(goal: str, *, department: str) -> dict:
    """Offline stand-in for the real department brain — no model, $0.

    Returns the same structured shape :func:`lhassa.org.executor.brain_executor`
    does, so the demo exercises the real message flow without spending tokens.
    """
    return {
        "ok": True,
        "summary": f"[offline] {department} brain would handle: {goal}",
        "department": department,
        "cost_usd": 0.0,
        "model": "fake-offline",
    }


async def _run() -> None:
    # Real SQLite, isolated in a temp dir (the durable typed queue + episodes).
    from lhassa.core import db

    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = Path(tmp) / "brain.db"
        await db.init_db()

        general = GeneralBrain(instance_id=_INSTANCE)

        depts = ", ".join(
            f"{d.name} ({d.description})" for d in routing.list_departments()
        )
        print(f"Registered departments: {depts}")
        print(f"Fallback department: {routing.FALLBACK_DEPARTMENT}\n")

        for goal in _GOALS:
            routed = routing.route(goal)
            print(f"GOAL: {goal!r}")
            print(f"  route() -> {routed}")

            # The general COORDINATES: it routes + enqueues a typed TASK_ASSIGN.
            assign = await general.assign_auto(goal, by_actor="owner")
            print(
                f"  TASK_ASSIGN  {assign.from_instance} -> {assign.to_instance} "
                f"(corr_id={assign.corr_id})"
            )

            # The department picks up its inbox and EXECUTES (injected fake executor).
            inbox = await messaging.inbox(
                department_identity(routed), instance_id=_INSTANCE, status="pending"
            )
            msg = next(m for m in inbox if m.id == assign.id)
            dept = DepartmentBrain(department=routed, instance_id=_INSTANCE)
            result = await dept.handle(msg, executor=_fake_executor)
            print(
                f"  TASK_RESULT  {result.from_instance} -> {result.to_instance}: "
                f"{result.payload['result']['summary']}"
            )

            # The general COLLECTS the correlated typed result.
            collected = await general.collect(assign.corr_id)
            print(f"  collect() -> {len(collected)} result(s)\n")

        print("Done — offline, deterministic, $0 spent.")


if __name__ == "__main__":
    asyncio.run(_run())
