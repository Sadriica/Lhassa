"""The two-brain core — a general that coordinates and a department that executes.

This is the minimal hierarchy where every Fase-7 problem appears with exactly TWO
brains (one general + one department), never six. The asymmetry is the whole
point:

  - :class:`GeneralBrain` **coordinates, it does NOT execute.** ``assign`` routes a
    typed ``TASK_ASSIGN`` to a department and records it; ``collect`` gathers the
    ``TASK_RESULT``\\ s. The general never runs the work itself — that avoids the
    bottleneck and keeps most decisions inside the department (the brief's golden
    rule, §3-Fase 7).
  - :class:`DepartmentBrain` **executes** with ITS OWN identity. ``handle`` runs the
    assigned work through an *injectable* ``executor`` that defaults to the real
    department brain (:func:`lhassa.org.executor.brain_executor`, offline by default
    so it stays test-safe and spends nothing) and returns a typed ``TASK_RESULT``
    correlated by ``corr_id``.
    Each handled task logs an ``Episode`` (``despertar_por="org"``) onto the audit
    plane — the episodes table already in place.

Multi-person approval (high-risk work): the proposer (the general's ``by_actor`` on
``assign``) is separated from the approver (passed to ``handle``). A high-risk task
self-approved by the SAME actor is rejected; a distinct approver lets it proceed.
This reuses :mod:`lhassa.security.approval` (parked at assign-time, resolved at
handle-time) — the existing HITL model, not a new one.

RBAC gates every privileged step via :mod:`lhassa.org.rbac`.
"""

import inspect
from functools import partial
from typing import Any, Awaitable, Callable, Optional, Union

from lhassa.config import Config
from lhassa.core.episode import Episode
from lhassa.org import messaging, rbac, routing
from lhassa.org.executor import brain_executor
from lhassa.org.messaging import Message, MessageKind
from lhassa.security import approval

# An executor is any callable ``(goal, *, department) -> result``. It may be sync
# or async; ``result`` is JSON-serialisable (typically a dict). Injectable so tests
# (and offline runs) need no real model.
Executor = Callable[..., Union[Any, Awaitable[Any]]]

GENERAL_IDENTITY = "general"


class SelfApprovalError(PermissionError):
    """Raised when a high-risk task's approver is missing or equals the proposer.

    Multi-person approval separates WHO PROPOSES from WHO APPROVES: a same-actor
    self-approval of high-risk work is never valid.
    """


def department_identity(department: str) -> str:
    """The logical brain identity for a department (``"dept:<name>"``)."""
    return f"dept:{department}"


async def _run_executor(executor: Executor, goal: str, *, department: str) -> Any:
    """Call ``executor`` (awaiting it if it is async) and return its result."""
    result = executor(goal, department=department)
    if inspect.isawaitable(result):
        result = await result
    return result


class GeneralBrain:
    """The coordinator brain — routes typed work, never executes it.

    Holds the shared org/tenant ``instance_id`` and the fixed identity
    ``"general"``. Its role is ``general`` for RBAC purposes.
    """

    role = "general"

    def __init__(
        self,
        *,
        instance_id: str = Config.instance_id,
        identity: str = GENERAL_IDENTITY,
    ) -> None:
        self.instance_id = instance_id
        self.identity = identity

    async def assign(
        self,
        goal: str,
        *,
        to_department: str,
        by_actor: str,
        high_risk: bool = False,
    ) -> Message:
        """Route a typed ``TASK_ASSIGN`` to a department and record it.

        The general COORDINATES: it enqueues the typed message and returns it; it
        does NOT execute the work. ``by_actor`` is the proposer (recorded for
        multi-person approval). For ``high_risk`` work an approval is parked in the
        existing approvals queue at assign-time; the department resolves it (with a
        DISTINCT approver) before executing.

        RBAC: only a ``general`` may assign.
        """
        rbac.require(self.role, "assign", "task")

        # Pre-generate the id so the assignment correlates to its own result.
        from lhassa.core import db

        msg_id = db._new_id("msg")
        to_instance = department_identity(to_department)
        payload: dict = {
            "goal": goal,
            "department": to_department,
            "proposer": by_actor,
            "high_risk": bool(high_risk),
        }

        if high_risk:
            approval_id = await approval.request_approval(
                self.instance_id,
                tipo="org_task",
                payload={
                    "accion": "org_task",
                    "actor_id": by_actor,
                    "goal": goal,
                    "department": to_department,
                    "corr_id": msg_id,
                },
            )
            payload["approval_id"] = approval_id

        msg = messaging.new_message(
            MessageKind.TASK_ASSIGN,
            from_instance=self.identity,
            to_instance=to_instance,
            payload=payload,
            corr_id=msg_id,
            msg_id=msg_id,
        )
        await messaging.enqueue(msg, instance_id=self.instance_id)
        return msg

    async def assign_auto(
        self,
        goal: str,
        *,
        by_actor: str,
        high_risk: bool = False,
    ) -> Message:
        """Route ``goal`` to the best department (deterministically) and assign it.

        Uses :func:`lhassa.org.routing.route` — a table-driven keyword match, NO
        LLM — to choose the target department, then delegates to :meth:`assign`. The
        general still only COORDINATES: it picks where the work goes and enqueues
        the typed ``TASK_ASSIGN``; it never executes the work itself.
        """
        to_department = routing.route(goal)
        return await self.assign(
            goal,
            to_department=to_department,
            by_actor=by_actor,
            high_risk=high_risk,
        )

    async def collect(self, corr_id: str) -> list[Message]:
        """Gather the ``TASK_RESULT``\\ (s) correlated to an assignment's ``corr_id``.

        RBAC: only a ``general`` may collect.
        """
        rbac.require(self.role, "collect", "task")
        return await messaging.by_corr(
            corr_id,
            instance_id=self.instance_id,
            kind=MessageKind.TASK_RESULT,
        )


class DepartmentBrain:
    """An executor brain — does the work with its OWN identity/department.

    Holds the shared org/tenant ``instance_id`` and its own logical identity
    (``"dept:<name>"``). Its role is ``department`` for RBAC purposes.
    """

    role = "department"

    def __init__(
        self,
        *,
        department: str,
        instance_id: str = Config.instance_id,
        identity: Optional[str] = None,
    ) -> None:
        self.department = department
        self.instance_id = instance_id
        self.identity = identity or department_identity(department)

    async def handle(
        self,
        message: Message,
        *,
        executor: Optional[Executor] = None,
        approver: Optional[str] = None,
    ) -> Message:
        """Execute an assigned ``TASK_ASSIGN`` and return a typed ``TASK_RESULT``.

        The department runs the work through ``executor`` (default: the real
        department brain :func:`brain_executor`, offline unless turned live) under
        ITS OWN identity — the general never runs here. The result is correlated to
        the assignment by ``corr_id`` and an
        ``Episode`` (``despertar_por="org"``) is written to the audit plane.

        Multi-person approval: for a ``high_risk`` assignment, ``approver`` must be
        present AND differ from the proposer; otherwise :class:`SelfApprovalError`
        is raised and nothing executes. The parked approval is resolved here.

        RBAC: only a ``department`` may handle a task.
        """
        rbac.require(self.role, "handle", "task")

        if message.kind is not MessageKind.TASK_ASSIGN:
            raise ValueError(
                f"department can only handle TASK_ASSIGN, got {message.kind.value}"
            )

        payload = message.payload or {}
        goal = payload.get("goal", "")
        proposer = payload.get("proposer")

        # ── Multi-person approval (separate proposer from approver) ──────────────
        if payload.get("high_risk"):
            if not approver or approver == proposer:
                raise SelfApprovalError(
                    "high-risk task needs a DISTINCT approver "
                    f"(proposer={proposer!r}, approver={approver!r})"
                )
            approval_id = payload.get("approval_id")
            if approval_id:
                await approval.resolve(
                    self.instance_id,
                    approval_id,
                    approved=True,
                    by_actor=approver,
                )

        # ── Execute on the DEPARTMENT side (never the general) ──────────────────
        # Default: the real department brain (``brain_executor``), bound to THIS
        # department's instance_id. It is offline by default (LHASSA_ORG_LIVE) so
        # the seam stays test/offline-safe; production turns it live or injects a
        # provider. Still fully injectable for tests/demos.
        if executor is None:
            executor = partial(brain_executor, instance_id=self.instance_id)
        result = await _run_executor(executor, goal, department=self.department)

        # ── Audit plane: one Episode per handled task ───────────────────────────
        ep = Episode.begin(despertar_por="org", instance_id=self.instance_id)
        ep.finish(
            decision=f"handle:{message.kind.value}",
            accion=f"{self.identity} executed task",
            resultado=str(result),
        )
        await ep.append()

        # ── Reply with a typed, correlated TASK_RESULT ──────────────────────────
        reply = messaging.new_message(
            MessageKind.TASK_RESULT,
            from_instance=self.identity,
            to_instance=message.from_instance,
            payload={
                "result": result,
                "goal": goal,
                "proposer": proposer,
                "department": self.department,
            },
            corr_id=message.corr_id,
        )
        await messaging.enqueue(reply, instance_id=self.instance_id)
        await messaging.mark(message.id, "done", instance_id=self.instance_id)
        return reply
