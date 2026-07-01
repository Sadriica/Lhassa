"""Deterministic delegation seam — a flow run flows THROUGH the org hierarchy.

The Telegram bridge and the flow API used to call :func:`lhassa.flows.engine.run_flow`
directly, bypassing the org's sovereignty (Lhassa is the permanent root
administrator; every flow is OWNED by some department beneath it). This module wraps
that call so a run instead flows down the command chain —
**Lhassa (root) → the owning department → the flow** — leaving an audit-trail
:class:`~lhassa.core.episode.Episode` at the delegation, WITHOUT any extra model call.

Two design rules mirror :mod:`lhassa.org.coordinator` / :mod:`lhassa.org.executor`
exactly:

  * **Deterministic + $0, never an LLM.** The owning department is derived by a pure,
    table/topology-driven lookup (the flow's pinned worker brains → their topology
    department), and the command PATH is read off the :class:`~lhassa.org.topology.
    Topology` tree. The only real cost stays the flow's pinned worker brains inside
    :func:`run_flow`; the delegation itself spends nothing.
  * **Governance must NEVER block the work.** Every hop degrades gracefully: if the
    topology/routing fails, the delegation is skipped (a debug line), the flow STILL
    runs directly, and the record is annotated with whatever chain was resolved (an
    empty one on total failure). :func:`delegate_flow` never raises.

The delegation :class:`Episode` (``despertar_por="org-delegate"``) lands on the same
audit plane :mod:`lhassa.org.monitor` reads, so the monitor sees the whole chain —
the org stays fully observable.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import db
from lhassa.core.episode import Episode

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import (breaks no cycle)
    from lhassa.flows.model import Flow
    from lhassa.org.topology import BrainNode, Topology

# The trigger label for a delegation episode — a distinct, greppable audit marker
# the monitor (and ``org/monitor.py``) picks up alongside the org's other episodes.
DESPERTAR_DELEGATE = "org-delegate"


# ── deterministic ownership derivation (never an LLM) ─────────────────────────────


def _pinned_task_brains(flow: "Flow") -> list[str]:
    """The pinned ``config.brain`` of every ``task`` node, in flow order.

    Only pinned brains anchor a flow to a department (an unpinned task auto-routes at
    run time and owns no fixed home). Defensive: a flow/node of an unexpected shape
    simply contributes nothing.
    """
    out: list[str] = []
    for node in getattr(flow, "nodes", None) or []:
        kind = getattr(node, "kind", None)
        kind_val = getattr(kind, "value", kind)
        if str(kind_val) != "task":
            continue
        cfg = getattr(node, "config", None) or {}
        brain = str(cfg.get("brain") or "").strip()
        if brain:
            out.append(brain)
    return out


def _parent_department(topology: "Topology", node: "BrainNode") -> Optional[str]:
    """The department of ``node``'s parent, if the parent exists and owns one."""
    parent_id = getattr(node, "parent", None)
    if parent_id and topology.has(parent_id):
        return topology.node(parent_id).department
    return None


def _department_for_brain(topology: "Topology", brain: str) -> Optional[str]:
    """Map a pinned brain to the department that owns it — deterministic, no LLM.

    Resolution order (each defensive): the brain as a node id → that node's own
    department (else its parent's department, else the node id); else a node whose
    ``department`` equals the brain; else a ``dept:<name>`` ↔ ``<name>`` tolerant
    match on either side. Returns ``None`` when nothing resolves.
    """
    if not brain:
        return None
    if topology.has(brain):
        node = topology.node(brain)
        return node.department or _parent_department(topology, node) or node.id
    for n in topology.nodes():
        if n.department and n.department == brain:
            return n.department
    stripped = brain.split(":", 1)[-1]
    for n in topology.nodes():
        if n.id.split(":", 1)[-1] == stripped or (n.department and n.department == stripped):
            return n.department or _parent_department(topology, n) or n.id
    return None


def _root_department(topology: "Topology") -> str:
    """The fallback owner: the root admin's department, else its id (Lhassa)."""
    root = topology.root()
    return root.department or root.id


def owning_department(flow: "Flow", topology: "Topology") -> str:
    """Derive the department that OWNS ``flow`` — deterministic, table-driven, $0.

    Collects the flow's ``task`` nodes' pinned ``config.brain`` values, maps each to
    its topology node's department (or the node / its parent department), and returns
    the shared / most-common department. When nothing resolves (no pinned brains, or
    a broken topology), it falls back to the topology root / ``config.root_admin_id``
    (Lhassa) — a flow always has an owner and this never raises.
    """
    try:
        fallback = _root_department(topology)
    except Exception:  # a degenerate topology — degrade to the config admin id
        from lhassa.org.topology import admin_id

        fallback = admin_id(None)

    departments: list[str] = []
    for brain in _pinned_task_brains(flow):
        try:
            dept = _department_for_brain(topology, brain)
        except Exception:
            dept = None
        if dept:
            departments.append(dept)

    if not departments:
        return fallback
    # most-common wins; Counter preserves first-seen order on ties (Python 3.7+).
    return Counter(departments).most_common(1)[0][0]


def _node_for_department(topology: "Topology", department: str) -> "Optional[BrainNode]":
    """The topology node that owns ``department`` (department match, then id)."""
    if not department:
        return None
    for n in topology.nodes():
        if n.department and n.department == department:
            return n
    for n in topology.nodes():
        if n.id == department:
            return n
    return None


def command_path(topology: "Topology", department: str) -> list[str]:
    """The command PATH of node ids from the root admin DOWN to ``department``.

    e.g. ``["lhassa", "general", "interns"]``. Walks parent links up from the owning
    node and reverses; a department with no locatable node degrades to just the root
    (``["lhassa"]``), and a broken topology to an empty chain. Deterministic, no LLM.
    """
    node = _node_for_department(topology, department)
    if node is None:
        try:
            return [topology.root().id]
        except Exception:
            return []
    chain: list[str] = []
    seen: set[str] = set()
    cur: "Optional[BrainNode]" = node
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        chain.append(cur.id)
        parent_id = getattr(cur, "parent", None)
        cur = topology.node(parent_id) if parent_id and topology.has(parent_id) else None
    chain.reverse()
    return chain


# ── the delegation episode (LLM-free, $0, never-raise) ────────────────────────────


async def _log_delegation(
    flow: "Flow", department: str, chain: list[str], *, config: Optional[Config]
) -> None:
    """Write ONE lightweight, LLM-free, $0 Episode capturing the delegation chain.

    ``despertar_por="org-delegate"``; the decision/action/result record who delegates
    what to whom and the root→…→dept path. No model call. Idempotent DB init so it
    works even before the brain is awake.
    """
    instance_id = getattr(config, "instance_id", None) or Config.instance_id
    flow_id = getattr(flow, "id", None)
    arrow = "→".join(chain) if chain else (department or "")
    await db.init_db()
    ep = Episode.begin(despertar_por=DESPERTAR_DELEGATE, instance_id=instance_id)
    ep.finish(
        decision=f"lhassa delegates flow {flow_id} down to {department}",
        accion=f"delegate:{arrow}",
        resultado=" → ".join(chain) if chain else str(department),
        costo_usd=0.0,
    )
    await ep.append()


# ── the seam callers go through ───────────────────────────────────────────────────


async def delegate_flow(
    flow: "Flow",
    *,
    inputs: Optional[dict] = None,
    config: Optional[Config] = None,
    company: Optional[str] = None,
    base_cv: Optional[str] = None,
) -> dict:
    """Run ``flow`` THROUGH the org hierarchy, then execute it. Returns the run record.

    Resolves the owning department, computes the command PATH from the root admin down
    to it, and logs one LLM-free ``org-delegate`` :class:`Episode` (the whole
    delegation, $0). Then runs the flow via a LAZY :func:`lhassa.flows.engine.run_flow`
    (the lazy import breaks the org↔flows cycle — both are shells) with the SAME
    arguments, so callers are unchanged. The returned record is annotated with
    ``record["delegation"] = {"chain": [...], "department": <dept>}`` and returned as-is
    otherwise.

    NEVER raises and NEVER blocks the work: if the topology/routing/episode step fails,
    it logs a debug line and STILL runs the flow directly (an empty/minimal chain) —
    governance and observability must never stop the actual execution.
    """
    chain: list[str] = []
    department = ""
    try:
        from lhassa.org import topology as topo_mod

        topology = topo_mod.load_topology(config)
        department = owning_department(flow, topology)
        chain = command_path(topology, department)
        await _log_delegation(flow, department, chain, config=config)
    except Exception as exc:  # governance/observability must never block the work
        logger.debug(
            "org: flow delegation degraded to direct run ({}): {}",
            getattr(flow, "id", None),
            exc,
        )

    # LAZY import: keep the org↔flows dependency one-directional at import time.
    from lhassa.flows.engine import run_flow

    record = await run_flow(
        flow, inputs=inputs, config=config, company=company, base_cv=base_cv
    )
    if isinstance(record, dict):
        record["delegation"] = {"chain": list(chain), "department": department}
    return record
