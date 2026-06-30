"""Org MANAGEMENT — create brains, define their skills/limits, and shape the chart.

This is the operations layer the owner drives through ``lhassa org`` (now) and the
dashboard org panel (a later slice) through ``/api/org/...``. It sits ON TOP of the
pure :mod:`lhassa.org.topology` model and turns the owner's intent into validated,
persisted mutations:

  - **CREATE BRAINS** — :func:`add_brain`.
  - **define the SKILLS they handle** — :func:`set_skills` / the ``skills`` argument.
  - **set their LIMITATIONS** — :func:`set_limits` / the ``limits`` argument.
  - **define how they ORGANIZE** — who commands whom (:func:`set_parent`, a re-parent)
    and who commands EVERYONE (:func:`set_root`, a re-root).
  - inspect the whole chart for a UI — :func:`describe`.

Every mutation is validated by constructing a :class:`Topology` (unique ids, one
root, every parent present, acyclic) BEFORE it is persisted, so a bad operation
raises and **never corrupts the saved file**. Persistence goes through
:func:`lhassa.org.topology.save_topology` (the configured ``org_topology_file``);
reads through :func:`lhassa.org.topology.load_topology` (so a fresh install with no
file still works off the derived default chart).

Skills are validated against the specialist registry (:mod:`lhassa.agents.specialists`)
where possible, but an unknown skill is ALLOWED with a warning — the org stays
modular and a plugin's domain can be assigned before its code is loaded.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Iterable, Mapping, Optional

from loguru import logger

from lhassa.org.topology import (
    BrainNode,
    Topology,
    load_topology,
    save_topology,
)

if TYPE_CHECKING:
    from lhassa.config import Config


class ManagementError(ValueError):
    """Raised on an invalid management operation (e.g. duplicate/missing brain).

    A :class:`ValueError` subclass — like
    :class:`lhassa.org.topology.TopologyError` — so the CLI/API can catch both with
    one ``except ValueError`` and map a bad op to a clear 4xx / exit-1 without ever
    touching the persisted file.
    """


# ── skills ────────────────────────────────────────────────────────────────────────


def available_skills() -> list[dict]:
    """The skills that CAN be assigned — the specialist registry's domains.

    Returns a serialisable list (``domain``/``name``/``tools``/``metric``) for the
    CLI ``lhassa org skills`` and the dashboard's skill picker. New domains appear
    automatically as they register (the elastic/modular seam), no edit here.
    """
    from lhassa.agents.specialists.registry import list_specialists

    return [
        {
            "domain": s.domain,
            "name": s.name,
            "tools": list(s.tools),
            "metric": s.metric_name,
        }
        for s in list_specialists()
    ]


def validate_skills(skills: Iterable[str]) -> tuple[tuple[str, ...], list[str]]:
    """Normalise ``skills`` and flag any not in the specialist registry.

    Returns ``(skills_tuple, unknown_list)``. Unknown skills are KEPT (the org is
    modular — a skill may be a plugin domain not yet loaded), only logged as a
    warning. Never raises.
    """
    from lhassa.agents.specialists.registry import get_specialist

    cleaned = tuple(s.strip() for s in (skills or ()) if s and s.strip())
    unknown = [s for s in cleaned if get_specialist(s) is None]
    if unknown:
        logger.warning(
            "org.management: assigning unregistered skill(s) {} — allowed (modular), "
            "but no specialist owns them yet.",
            unknown,
        )
    return cleaned, unknown


# ── internal helpers ────────────────────────────────────────────────────────────────


def _persist(nodes: Iterable[BrainNode], config: "Optional[Config]") -> Topology:
    """Build a :class:`Topology` (VALIDATES) from ``nodes`` then save it.

    Construction is the validation gate: an invalid set of nodes raises here, before
    :func:`save_topology` is ever reached, so the on-disk file is never corrupted.
    """
    updated = Topology(list(nodes))
    save_topology(updated, config)
    return updated


def _replace_one(
    topology: Topology, node_id: str, new_node: BrainNode
) -> list[BrainNode]:
    """The node list with ``node_id`` swapped for ``new_node`` (order preserved)."""
    return [new_node if n.id == node_id else n for n in topology.nodes()]


# ── create / update / remove ────────────────────────────────────────────────────────


def add_brain(
    node_id: str,
    *,
    role: str = "department",
    parent: Optional[str] = None,
    department: Optional[str] = None,
    skills: Iterable[str] = (),
    limits: Optional[Mapping] = None,
    config: "Optional[Config]" = None,
) -> Topology:
    """Create a brain and persist it. Returns the updated topology.

    Rejects a duplicate id (before save), a missing parent, or a cycle (caught by
    :class:`Topology` validation). ``parent=None`` is only valid when there is no
    other root (i.e. the very first brain).
    """
    node_id = (node_id or "").strip()
    if not node_id:
        raise ManagementError("a brain needs a non-empty id")
    topology = load_topology(config)
    if topology.has(node_id):
        raise ManagementError(f"brain {node_id!r} already exists")
    skills, _ = validate_skills(skills)
    node = BrainNode(
        id=node_id,
        role=(role or "department"),
        department=department or None,
        parent=parent or None,
        skills=skills,
        limits=dict(limits or {}),
    )
    return _persist([*topology.nodes(), node], config)


def update_brain(
    node_id: str,
    *,
    skills: Optional[Iterable[str]] = None,
    limits: Optional[Mapping] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    config: "Optional[Config]" = None,
) -> Topology:
    """Partially update a brain (only the provided fields change). Returns topology.

    A ``None`` argument means "leave unchanged". To CLEAR the department pass an
    empty string. ``skills``/``limits`` replace (not merge) the field when given.
    """
    topology = load_topology(config)
    node = topology.node(node_id)  # KeyError if absent

    changes: dict = {}
    if skills is not None:
        clean, _ = validate_skills(skills)
        changes["skills"] = clean
    if limits is not None:
        if not isinstance(limits, Mapping):
            raise ManagementError("limits must be a mapping/object")
        changes["limits"] = dict(limits)
    if role is not None:
        changes["role"] = role or "department"
    if department is not None:
        changes["department"] = department or None

    new_node = replace(node, **changes)
    return _persist(_replace_one(topology, node_id, new_node), config)


def remove_brain(
    node_id: str,
    *,
    force: bool = False,
    config: "Optional[Config]" = None,
) -> Topology:
    """Remove a brain. Returns the updated topology.

    Refuses to ORPHAN children (or to drop a populated root) unless ``force`` — with
    ``force`` the whole subtree under ``node_id`` is removed. Removing the last brain
    (an empty chart) is always rejected by :class:`Topology` validation, so the file
    is never left empty.
    """
    topology = load_topology(config)
    if not topology.has(node_id):
        raise ManagementError(f"no brain {node_id!r} in the topology")
    children = topology.children_of(node_id)
    if children and not force:
        kids = ", ".join(c.id for c in children)
        raise ManagementError(
            f"brain {node_id!r} has children ({kids}); refusing to orphan them — "
            "pass force to remove the whole subtree."
        )
    drop = {node_id} | {d.id for d in topology.descendants(node_id)}
    remaining = [n for n in topology.nodes() if n.id not in drop]
    return _persist(remaining, config)


# ── organize: who commands whom / who commands everyone ─────────────────────────────


def set_parent(
    node_id: str,
    new_parent: str,
    *,
    config: "Optional[Config]" = None,
) -> Topology:
    """RE-PARENT a brain — change WHO COMMANDS WHOM. Returns the updated topology.

    Rejects self-parenting and a missing parent; a move that would create a cycle
    (under one's own descendant) is caught by :class:`Topology` validation. Note the
    root cannot be re-parented this way (it would leave the chart rootless) — use
    :func:`set_root` to change who commands everyone.
    """
    topology = load_topology(config)
    node = topology.node(node_id)  # KeyError if absent
    if new_parent == node_id:
        raise ManagementError("a brain cannot be its own parent")
    if not topology.has(new_parent):
        raise ManagementError(f"new parent {new_parent!r} does not exist")
    new_node = replace(node, parent=new_parent)
    return _persist(_replace_one(topology, node_id, new_node), config)


def set_root(
    node_id: str,
    *,
    config: "Optional[Config]" = None,
) -> Topology:
    """Promote a brain to ROOT — make it the one that COMMANDS EVERYONE.

    Re-roots the tree by REVERSING the spine from ``node_id`` up to the old root:
    ``node_id`` becomes the root (``parent=None``) and each ancestor on that path
    becomes a child of the next node down the path — so the old root ends up as a
    child of the node directly below it. Every brain NOT on that path keeps its
    parent, so all other subtree relationships are preserved intact.

    If ``node_id`` is already the root this is a no-op (the chart is returned
    unchanged, not rewritten). Returns the updated topology.
    """
    topology = load_topology(config)
    node = topology.node(node_id)  # KeyError if absent
    if node.parent is None:
        return topology  # already commands everyone

    # Walk the spine node → … → old root.
    path: list[BrainNode] = []
    cur: Optional[BrainNode] = node
    while cur is not None:
        path.append(cur)
        cur = topology.node(cur.parent) if cur.parent else None

    new_parent: dict[str, Optional[str]] = {n.id: n.parent for n in topology.nodes()}
    new_parent[path[0].id] = None  # the promoted brain is the new root
    for i in range(1, len(path)):
        new_parent[path[i].id] = path[i - 1].id  # each ancestor flips downward

    nodes = [replace(n, parent=new_parent[n.id]) for n in topology.nodes()]
    return _persist(nodes, config)


# ── convenience wrappers ────────────────────────────────────────────────────────────


def set_skills(
    node_id: str,
    skills: Iterable[str],
    *,
    config: "Optional[Config]" = None,
) -> Topology:
    """Define the SKILLS a brain handles (replaces the set). Returns the topology."""
    return update_brain(node_id, skills=skills, config=config)


def set_limits(
    node_id: str,
    limits: Mapping,
    *,
    config: "Optional[Config]" = None,
) -> Topology:
    """Set a brain's LIMITATIONS (replaces the mapping). Returns the topology."""
    return update_brain(node_id, limits=limits, config=config)


# ── inspect ─────────────────────────────────────────────────────────────────────────


def describe(config: "Optional[Config]" = None) -> dict:
    """The whole org chart as a serialisable dict (for the API / dashboard).

    Shape::

        {
          "root": "<root id>",
          "standalone": bool,
          "nodes": [
            {"id", "role", "department", "parent", "skills": [...], "limits": {...},
             "children": [child ids], "is_leaf": bool},
            ...
          ]
        }

    Node order is the topology's deterministic insertion order.
    """
    topology = load_topology(config)
    nodes = [
        {
            "id": n.id,
            "role": n.role,
            "department": n.department,
            "parent": n.parent,
            "skills": list(n.skills),
            "limits": dict(n.limits),
            "children": [c.id for c in topology.children_of(n.id)],
            "is_leaf": topology.is_leaf(n.id),
        }
        for n in topology.nodes()
    ]
    return {
        "root": topology.root().id,
        "standalone": topology.is_standalone(),
        "nodes": nodes,
    }
