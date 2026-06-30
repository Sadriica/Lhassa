"""
The watchlist — the deterministic "what woke me?" check (§2.4).

Every tick, ``check()`` scans state in code with ZERO tokens and returns a list
of ``Trigger``s. Nothing to do → empty list → the brain sleeps. A trigger that
maps to a fixed action (``maps_to_fixed_action``) runs deterministically via
``actions.run``; one that does not is deferred to the Phase 2 router.

The brief (§2.4) lists the eventual trigger sources: a machine reported, a
file/repo changed, a task came due, a job finished, a threshold was crossed, a
message matched a rule. For Phase 0 the only source is **pending tasks** in the
brain DB. ``check()`` is structured as a small list of independent check-funcs
that each append Triggers, so the rest slot in later without reshaping the loop.
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from lhassa.config import Config
from lhassa.core import actions, db


@dataclass
class Trigger:
    """
    One reason the brain woke up, with everything needed to act on it.

    ``kind``         — the source/category ("task"; later "repo", "job", …).
    ``action``       — the typed action to take (closed set in ``actions.ACTIONS``
                       when fixed; otherwise a label the Phase 2 router resolves).
    ``objetivo``     — the target of the action (e.g. a repo/project name).
    ``params``       — action parameters.
    ``task_id``      — the originating task id, when the trigger came from a task.
    ``origen_actor`` — QUIÉN originated it (§2.6 hook for roles), if known.
    """

    kind: str
    action: str
    objetivo: str = ""
    params: dict = field(default_factory=dict)
    task_id: Optional[str] = None
    origen_actor: Optional[str] = None

    def maps_to_fixed_action(self) -> bool:
        """True iff ``action`` is in the closed deterministic set (no LLM needed)."""
        return self.action in actions.ACTIONS


# ── check functions (each appends Triggers; ZERO tokens) ─────────────────────────

async def _from_pending_tasks(instance_id: str, out: list[Trigger]) -> None:
    """Pending tasks in the brain DB → one Trigger per task (§2.4 source for Phase 0)."""
    for task in await db.load_pending_tasks(instance_id):
        out.append(
            Trigger(
                kind="task",
                action=task.get("accion", ""),
                objetivo=task.get("objetivo", "") or "",
                params=task.get("params", {}) or {},
                task_id=task.get("id"),
                origen_actor=task.get("origen_actor"),
            )
        )


# Ordered list of deterministic checks. Append more here (repo changed, job
# finished, threshold crossed, message matched) without touching ``check``.
_CHECKS: list[Callable[[str, list[Trigger]], Awaitable[None]]] = [
    _from_pending_tasks,
]


async def check(instance_id: Optional[str] = None) -> list[Trigger]:
    """
    Run every deterministic check and return the collected Triggers (ZERO tokens).
    No triggers → empty list (the brain sleeps). Defaults ``instance_id`` to the
    configured instance so callers can wake the watchlist with no arguments.
    """
    instance_id = instance_id or Config.instance_id
    triggers: list[Trigger] = []
    for check_fn in _CHECKS:
        await check_fn(instance_id, triggers)
    return triggers
