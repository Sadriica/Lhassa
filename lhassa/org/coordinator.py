# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two-brain core; a general that coordinates and a department that executes.

This is the minimal hierarchy where every Fase-7 problem appears with exactly TWO
brains (one general + one department), never six. The asymmetry is the whole
point:

  - :class:`GeneralBrain` **coordinates, it does NOT execute.** ``assign`` routes a
    typed ``TASK_ASSIGN`` to a department and records it; ``collect`` gathers the
    ``TASK_RESULT``\\ s. The general never runs the work itself; that avoids the
    bottleneck and keeps most decisions inside the department (the brief's golden
    rule, §3-Fase 7).
  - :class:`DepartmentBrain` **executes** with ITS OWN identity. ``handle`` runs the
    assigned work through an *injectable* ``executor`` that defaults to the real
    department brain (:func:`lhassa.org.executor.brain_executor`, offline by default
    so it stays test-safe and spends nothing) and returns a typed ``TASK_RESULT``
    correlated by ``corr_id``.
    Each handled task logs an ``Episode`` (``despertar_por="org"``) onto the audit
    plane; the episodes table already in place.

Multi-person approval (high-risk work): the proposer (the general's ``by_actor`` on
``assign``) is separated from the approver (passed to ``handle``). A high-risk task
self-approved by the SAME actor is rejected; a distinct approver lets it proceed.
This reuses :mod:`lhassa.security.approval` (parked at assign-time, resolved at
handle-time); the existing HITL model, not a new one.

RBAC gates every privileged step via :mod:`lhassa.org.rbac`.
"""

import inspect
import os
import re
from functools import partial
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, Union

from loguru import logger

from lhassa.config import Config
from lhassa.core.episode import Episode
from lhassa.org import messaging, rbac, routing
from lhassa.org.executor import brain_executor
from lhassa.org.messaging import Message, MessageKind
from lhassa.security import approval

# Truthy values that turn the live reasoning path on; identical to the rest of
# the org layer's gate (executor.py / monitor.py): default OFF → offline + $0.
_TRUTHY = {"1", "true", "yes", "on"}

# Fan-out cap: at most this many departments run a goal in parallel (a director
# general delegates WIDE, not unboundedly; mirrors dispatch._MAX_TASKS's spirit).
_MULTI_TOP_K = 3


def _live_enabled(config: "Optional[Config]") -> bool:
    """Whether the live reasoning path is on (``LHASSA_ORG_LIVE``; default OFF)."""
    return (os.getenv("LHASSA_ORG_LIVE", "") or "").strip().lower() in _TRUTHY

if TYPE_CHECKING:
    from lhassa.org.topology import BrainNode, Topology

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
    """The coordinator brain; routes typed work, never executes it.

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

        Uses :func:`lhassa.org.routing.route`: a table-driven keyword match, NO
        LLM; to choose the target department, then delegates to :meth:`assign`. The
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
    """An executor brain; does the work with its OWN identity/department.

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
        ITS OWN identity; the general never runs here. The result is correlated to
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
                try:
                    await approval.resolve(
                        self.instance_id,
                        approval_id,
                        approved=True,
                        by_actor=approver,
                        approver=approval.TrustedApprover(approver, "org-rbac"),
                    )
                except (KeyError, approval.ApprovalResolutionConflict) as exc:
                    # A stale or raced approval id (duplicate delivery, resolved
                    # elsewhere) must not abort the coordination walk; the
                    # distinct-approver gate above already held.
                    logger.warning(
                        f"org: approval {approval_id!r} not resolvable ({exc}); "
                        "continuing"
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


# ══════════════════════════════════════════════════════════════════════════════════
# Topology-aware coordination; walk a CHART of 1..N brains (slice E).
#
# The two classes above are the special case (one general + departments). The
# functions below GENERALISE coordination to an arbitrary :class:`Topology` tree:
# starting at the root, each coordinator ROUTES down to one child via a typed
# ``TASK_ASSIGN``; only a LEAF executes (via the injectable executor /
# ``brain_executor``); the ``TASK_RESULT`` bubbles back up to the root. A single-
# brain topology executes directly (it is its own leaf).
#
# RBAC is preserved by COORDINATING vs EXECUTING, not by the node's label: a node
# with children only assigns (the ``general`` permission), a leaf only handles (the
# ``department`` permission via :class:`DepartmentBrain`). A coordinator NEVER calls
# an executor; that is the golden rule, now enforced at every level of the tree.
# ══════════════════════════════════════════════════════════════════════════════════


def _name_keywords(name: str) -> set[str]:
    """Tokens of an id/department name usable as routing keywords (len ≥ 3)."""
    return {tok for tok in re.split(r"[^a-z0-9]+", (name or "").lower()) if len(tok) >= 3}


def _subtree_keywords(topology: "Topology", child: "BrainNode") -> frozenset[str]:
    """The merged routing keywords a child OWNS; its own + its whole subtree's.

    A coordinator routes a goal into the subtree whose departments best match it, so
    a generic sub-coordinator still routes correctly when one of its descendants owns
    the matching department. Pulls registered :class:`routing.Department` keyword
    tables when present, else synthesises keywords from the name. NO LLM.
    """
    names: list[str] = [child.department or child.id]
    names += [d.department for d in topology.descendants(child.id) if d.department]
    kws: set[str] = set()
    for name in names:
        reg = routing.get_department(name)
        if reg is not None:
            kws |= set(reg.keywords)
        kws |= _name_keywords(name)
    return frozenset(kws)


def route_scores_in_topology(
    topology: "Topology", parent_id: str, goal: str
) -> "list[tuple[BrainNode, int]]":
    """Score every CHILD of ``parent_id`` against ``goal``: deterministic, no LLM.

    Returns ``[(child, score), ...]`` sorted best-first; a stable sort preserves
    the children's declared order among ties (the first-declared child wins a
    tie, matching :func:`route_in_topology`). Exposed separately from
    :func:`route_in_topology` so a caller (fan-out) can inspect the WHOLE ranking,
    not just the winner; the same keyword-scoring policy as
    :func:`lhassa.org.routing.route`.
    """
    children = topology.children_of(parent_id)
    if not children:
        raise ValueError(f"brain {parent_id!r} has no children to route to")
    goal_lower = (goal or "").lower()
    scored: list[tuple["BrainNode", int]] = []
    for child in children:
        dept = routing.Department(
            name=child.department or child.id,
            keywords=_subtree_keywords(topology, child),
        )
        scored.append((child, routing._score(goal_lower, dept)))
    # Stable sort: equal scores keep their original (declared) relative order.
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def route_in_topology(topology: "Topology", parent_id: str, goal: str) -> str:
    """Pick which CHILD of ``parent_id`` a goal routes to; deterministic, no LLM.

    Scores ``goal`` against each child's subtree keywords (its own department plus
    its descendants') and returns the best child's id. Ties break by child order
    (the first-declared child wins), so routing is stable and free. Reuses the same
    keyword-scoring policy as :func:`lhassa.org.routing.route`.
    """
    return route_scores_in_topology(topology, parent_id, goal)[0][0].id


# ── fan-out synthesis (offline $0 merge; live = one extra think) ──────────────


def _offline_synthesis(goal: str, branches: "list[dict]") -> str:
    """Deterministic, $0 merge of per-department results; no model, no cost.

    One header per department followed by its result, in assignment order. This
    is the default (offline) synthesis; :func:`_live_synthesis` is the
    ``LHASSA_ORG_LIVE`` alternative.
    """
    lines = [f"Synthesis for: {goal}"]
    for b in branches:
        lines.append(f"- {b['department']}: {b['result']}")
    return "\n".join(lines)


async def _live_synthesis(
    goal: str, branches: "list[dict]", *, instance_id: str, config: Optional[Config]
) -> str:
    """One extra think by the root/general brain over the collected branch results.

    Only reached under ``LHASSA_ORG_LIVE``; a failure (missing provider, budget,
    anything) degrades to :func:`_offline_synthesis`: synthesis must never crash
    or block the (already-collected) branch results.
    """
    try:
        compact = "\n".join(f"{b['department']}: {b['result']}" for b in branches)
        prompt = (
            f"Synthesize the following department results for the goal {goal!r} "
            f"into one concise answer:\n\n{compact}"
        )
        res = await brain_executor(
            prompt, department=GENERAL_IDENTITY, instance_id=instance_id, config=config
        )
        return str(res.get("summary") or "").strip() or _offline_synthesis(goal, branches)
    except Exception:
        return _offline_synthesis(goal, branches)


async def _log_synthesis_episode(
    goal: str, branches: "list[dict]", synthesis: str, *, live: bool, instance_id: str
) -> None:
    """One Episode for the fan-out + synthesis step ($0 regardless of ``live``).

    ``live`` only describes whether the SYNTHESIS text came from a model; the
    episode itself never calls one; this is bookkeeping, not thinking.
    """
    depts = ", ".join(b["department"] for b in branches)
    ep = Episode.begin(despertar_por="org", instance_id=instance_id)
    ep.finish(
        decision=f"synthesis: {'live' if live else 'offline'}",
        accion=f"fan-out synthesis over [{depts}]",
        resultado=synthesis[:2000],
    )
    await ep.append()


async def _make_assign(
    *,
    from_id: str,
    to_id: str,
    goal: str,
    by_actor: str,
    high_risk: bool,
    corr_id: str,
    instance_id: str,
) -> Message:
    """Build, park-approval-if-needed, and enqueue a typed ``TASK_ASSIGN`` hop.

    Mirrors :meth:`GeneralBrain.assign` (multi-person approval parked at assign-time
    for high-risk work) but addresses an explicit topology id rather than the
    ``dept:<name>`` shape, so it works at any level of the tree.
    """
    from lhassa.core import db

    rbac.require("general", "assign", "task")  # coordinators (only) may assign
    msg_id = db._new_id("msg")
    payload: dict = {"goal": goal, "proposer": by_actor, "high_risk": bool(high_risk)}
    if high_risk:
        payload["approval_id"] = await approval.request_approval(
            instance_id,
            tipo="org_task",
            payload={
                "accion": "org_task",
                "actor_id": by_actor,
                "goal": goal,
                "corr_id": corr_id,
            },
        )
    msg = messaging.new_message(
        MessageKind.TASK_ASSIGN,
        from_instance=from_id,
        to_instance=to_id,
        payload=payload,
        corr_id=corr_id,
        msg_id=msg_id,
    )
    await messaging.enqueue(msg, instance_id=instance_id)
    return msg


async def _execute_leaf(
    node: "BrainNode",
    assign: Message,
    *,
    executor: Optional[Executor],
    approver: Optional[str],
    instance_id: str,
) -> Any:
    """Run an assignment on a LEAF brain via :class:`DepartmentBrain` (it executes).

    Reuses the slice-1 execution path verbatim: the leaf handles the typed
    ``TASK_ASSIGN`` (injectable executor / ``brain_executor`` default), enforces
    multi-person approval, logs the ``despertar_por="org"`` episode, and replies
    with a correlated ``TASK_RESULT``. Returns the result payload.
    """
    dept = DepartmentBrain(
        department=node.department or node.id,
        instance_id=instance_id,
        identity=node.id,
    )
    reply = await dept.handle(assign, executor=executor, approver=approver)
    return reply.payload["result"]


async def _walk(
    topology: "Topology",
    coordinator: "BrainNode",
    goal: str,
    *,
    corr_id: str,
    by_actor: str,
    executor: Optional[Executor],
    approver: Optional[str],
    high_risk: bool,
    instance_id: str,
    path: list[str],
) -> tuple[Any, str]:
    """Coordinate from ``coordinator`` down to a leaf, returning ``(result, leaf_id)``.

    ``coordinator`` COORDINATES only: it routes to one child and enqueues a typed
    ``TASK_ASSIGN``. A leaf child executes; a sub-coordinator child recurses and then
    bubbles its subtree's ``TASK_RESULT`` back up to this coordinator. Never executes.
    """
    child_id = route_in_topology(topology, coordinator.id, goal)
    child = topology.node(child_id)
    path.append(child_id)
    child_is_leaf = topology.is_leaf(child_id)

    # Only the executing hop (to a leaf) carries high-risk + a parked approval.
    assign = await _make_assign(
        from_id=coordinator.id,
        to_id=child.id,
        goal=goal,
        by_actor=by_actor,
        high_risk=high_risk and child_is_leaf,
        corr_id=corr_id,
        instance_id=instance_id,
    )

    if child_is_leaf:
        result = await _execute_leaf(
            child, assign, executor=executor, approver=approver, instance_id=instance_id
        )
        return result, child.id

    # Sub-coordinator: recurse, then bubble the subtree result up one hop.
    result, leaf_id = await _walk(
        topology,
        child,
        goal,
        corr_id=corr_id,
        by_actor=by_actor,
        executor=executor,
        approver=approver,
        high_risk=high_risk,
        instance_id=instance_id,
        path=path,
    )
    reply = messaging.new_message(
        MessageKind.TASK_RESULT,
        from_instance=child.id,
        to_instance=coordinator.id,
        payload={"result": result, "goal": goal, "department": leaf_id},
        corr_id=corr_id,
    )
    await messaging.enqueue(reply, instance_id=instance_id)
    await messaging.mark(assign.id, "done", instance_id=instance_id)
    return result, leaf_id


async def _run_branch(
    topology: "Topology",
    root: "BrainNode",
    child: "BrainNode",
    goal: str,
    *,
    by_actor: str,
    executor: Optional[Executor],
    approver: Optional[str],
    high_risk: bool,
    instance_id: str,
) -> tuple[Any, str, list[str]]:
    """Run ``goal`` down ONE of root's children (single-route beneath it).

    Reuses :func:`_execute_leaf` / :func:`_walk` exactly as the single-winner path
    does; a fan-out branch is otherwise indistinguishable from a normal single
    assignment. Returns ``(result, leaf_id, branch_path)``.
    """
    from lhassa.core import db

    branch_path = [root.id, child.id]
    corr_id = db._new_id("msg")
    if topology.is_leaf(child.id):
        assign = await _make_assign(
            from_id=root.id,
            to_id=child.id,
            goal=goal,
            by_actor=by_actor,
            high_risk=high_risk,
            corr_id=corr_id,
            instance_id=instance_id,
        )
        result = await _execute_leaf(
            child, assign, executor=executor, approver=approver, instance_id=instance_id
        )
        return result, child.id, branch_path

    result, leaf_id = await _walk(
        topology,
        child,
        goal,
        corr_id=corr_id,
        by_actor=by_actor,
        executor=executor,
        approver=approver,
        high_risk=high_risk,
        instance_id=instance_id,
        path=branch_path,
    )
    return result, leaf_id, branch_path


async def run_goal(
    goal: str,
    *,
    by_actor: str,
    topology: "Optional[Topology]" = None,
    config: Optional[Config] = None,
    executor: Optional[Executor] = None,
    approver: Optional[str] = None,
    high_risk: bool = False,
    instance_id: Optional[str] = None,
    params: Optional[dict] = None,
) -> dict:
    """Run ``goal`` end-to-end across an org topology (1..N brains, any depth).

    Loads the chart (``topology`` or :func:`lhassa.org.topology.load_topology`),
    starts at the root and routes DOWN to the appropriate leaf via typed
    ``TASK_ASSIGN`` messages at each hop; the leaf executes (injectable executor /
    ``brain_executor`` default, offline unless ``LHASSA_ORG_LIVE``); the
    ``TASK_RESULT`` bubbles back to the root. A single-brain topology executes
    directly. Returns a structured dict: ``goal``, ``standalone``, ``path`` (root→
    leaf ids), ``leaf``, ``department``, ``result`` and ``corr_id``.

    **Fan-out (root level only).** When the root's children score a TIE at the
    top of :func:`route_scores_in_topology` (more than one department equally-
    best for ``goal``), or ``params={"multi": True}`` explicitly asks for it, the
    goal is assigned to the top :data:`_MULTI_TOP_K` children INSTEAD of just the
    winner; each branch still routes single-file beneath its own child (no
    deeper multi-level fan-out; a deliberate scope decision to keep this
    tractable). All branch ``TASK_RESULT``\\ s are collected and merged into ONE
    synthesis; a deterministic, $0 per-department merge by default, or one
    extra think by the root/general brain under ``LHASSA_ORG_LIVE``. A single
    clear winner (the common case) is unaffected: same cost, same shape as
    before. The synthesis step logs exactly one ``Episode``.
    """
    from lhassa.core import db
    from lhassa.org import topology as topo_mod

    config = config or Config()
    params = params or {}
    if topology is None:
        topology = topo_mod.load_topology(config)
    instance_id = (
        instance_id or getattr(config, "instance_id", None) or Config.instance_id
    )
    await db.init_db()

    root = topology.root()
    path = [root.id]
    corr_id = db._new_id("msg")

    if topology.is_leaf(root.id):
        # Single-brain / leaf root: it IS the executor; there is no one above it.
        assign = await _make_assign(
            from_id=by_actor,
            to_id=root.id,
            goal=goal,
            by_actor=by_actor,
            high_risk=high_risk,
            corr_id=corr_id,
            instance_id=instance_id,
        )
        result = await _execute_leaf(
            root, assign, executor=executor, approver=approver, instance_id=instance_id
        )
        leaf_node = topology.node(root.id)
        return {
            "goal": goal,
            "standalone": topology.is_standalone(),
            "path": [root.id],
            "leaf": root.id,
            "department": leaf_node.department or root.id,
            "result": result,
            "corr_id": corr_id,
        }

    scores = route_scores_in_topology(topology, root.id, goal)
    top_score = scores[0][1] if scores else 0
    tied = [child for child, score in scores if score == top_score and top_score > 0]
    wants_multi = bool(params.get("multi"))
    fan_out = len(scores) > 1 and (wants_multi or len(tied) > 1)

    if fan_out:
        chosen = [child for child, _ in scores[:_MULTI_TOP_K]]
        branches: list[dict] = []
        for child in chosen:
            result, leaf_id, branch_path = await _run_branch(
                topology, root, child, goal,
                by_actor=by_actor, executor=executor, approver=approver,
                high_risk=high_risk, instance_id=instance_id,
            )
            leaf_node = topology.node(leaf_id)
            branches.append(
                {
                    "department": leaf_node.department or leaf_id,
                    "leaf": leaf_id,
                    "path": branch_path,
                    "result": result,
                }
            )

        live = _live_enabled(config)
        if live:
            synthesis = await _live_synthesis(
                goal, branches, instance_id=instance_id, config=config
            )
        else:
            synthesis = _offline_synthesis(goal, branches)
        await _log_synthesis_episode(
            goal, branches, synthesis, live=live, instance_id=instance_id
        )

        return {
            "goal": goal,
            "standalone": topology.is_standalone(),
            "multi": True,
            "path": [root.id] + [b["leaf"] for b in branches],
            "leaf": [b["leaf"] for b in branches],
            "department": [b["department"] for b in branches],
            "branches": branches,
            "result": synthesis,
            "corr_id": corr_id,
        }

    result, leaf_id = await _walk(
        topology,
        root,
        goal,
        corr_id=corr_id,
        by_actor=by_actor,
        executor=executor,
        approver=approver,
        high_risk=high_risk,
        instance_id=instance_id,
        path=path,
    )

    leaf_node = topology.node(leaf_id)
    return {
        "goal": goal,
        "standalone": topology.is_standalone(),
        "path": path,
        "leaf": leaf_id,
        "department": leaf_node.department or leaf_id,
        "result": result,
        "corr_id": corr_id,
    }
