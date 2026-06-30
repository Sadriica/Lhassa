"""The org CHART — a config-driven brain TOPOLOGY (Fase 7, slice E).

Slices 1–2 shipped the minimal two-brain core (one ``general`` + departments). This
module generalises the SHAPE of the org so the owner can run **1 brain OR N brains**
and define the **hierarchical flow** those brains have — config-driven and
user-manageable, NOT hard-coded general→department.

The model is a TREE of :class:`BrainNode` descriptors:

  - A **single standalone brain** is the trivial tree: one root, no children. It
    coordinates *and* executes (there is no one else).
  - A **flat org** is a root coordinator with department leaves (the slice-1 shape).
  - An **arbitrary-depth hierarchy** is any tree: a root that delegates to
    sub-coordinators that delegate to departments, to any depth.

Where a chart comes from:

  - If ``config.org_topology_file`` exists, it is the source of truth (JSON: a list
    of node objects). The owner creates/edits it via ``lhassa org`` or by hand.
  - Otherwise a default chart is DERIVED from config — a single standalone brain
    when ``org_role`` is empty, else a flat ``general`` → ``[org_departments…]``
    chart (back-compatible with the existing two-brain behaviour).

A coordinator COORDINATES, it never executes — only leaves/departments execute.
That asymmetry is enforced in :mod:`lhassa.org.coordinator` (which walks this tree);
this module is the pure, side-effect-free shape + its load/save.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping, Optional

if TYPE_CHECKING:
    from lhassa.config import Config

# Open role vocabulary — kept as plain strings so the owner can name roles freely
# (e.g. "general", "department", "sub_general"). RBAC is enforced by COORDINATING
# vs EXECUTING (a node with children coordinates; a leaf executes), not by the
# label, so new role names never need an RBAC edit.
ROLE_GENERAL = "general"
ROLE_DEPARTMENT = "department"
# The role of the PERMANENT root administrator brain (Lhassa). It is never a leaf
# "executor" label — it sits above any org and coordinates everything beneath it.
ROLE_ADMIN = "administrator"

# Fallback admin id when a Config (or a stand-in) does not carry ``root_admin_id``.
DEFAULT_ADMIN_ID = "lhassa"


class TopologyError(ValueError):
    """Raised when a set of :class:`BrainNode`\\ s is not a valid org tree."""


@dataclass(frozen=True)
class BrainNode:
    """One brain in the org chart (immutable).

    Fields:
      - ``id``: the logical brain id (e.g. ``"general"``, ``"dept:optimizacion"``).
        Unique within a topology; doubles as the messaging identity.
      - ``role``: an open label (``"general"``/``"department"``/``"sub_general"``…).
      - ``department``: the department name/identity this brain owns (optional —
        coordinators may have none; leaves usually do).
      - ``parent``: the id of the parent brain, or ``None`` for the single root.
      - ``skills``: the skill/specialist domains this brain HANDLES (e.g.
        ``("optimizacion", "finanzas")``). Free-form strings so the org stays
        modular; management validates them against the specialist registry where
        it can and merely warns on an unknown skill. Stored as a tuple (hashable).
      - ``limits``: the brain's LIMITATIONS — an opaque, forward-compatible mapping
        (e.g. ``{"max_risk": "high|medium|low", "budget_usd": 1.0,
        "allowed_actions": [...]}``). Unknown keys are kept verbatim. Excluded from
        the node hash so adding limits never breaks hashing of a frozen node.

    ``children`` are not stored on the node (the tree owns that relation); use
    :meth:`Topology.children_of`.
    """

    id: str
    role: str = ROLE_GENERAL
    department: Optional[str] = None
    parent: Optional[str] = None
    skills: tuple[str, ...] = ()
    limits: Mapping = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        # Normalise the additive fields so callers may pass a list/None and the
        # node stays hashable (skills→tuple) and JSON-friendly (limits→dict). The
        # dataclass is frozen, so mutate through object.__setattr__.
        object.__setattr__(self, "skills", tuple(self.skills or ()))
        object.__setattr__(self, "limits", dict(self.limits or {}))

    def to_dict(self) -> dict:
        """Serialise to the JSON node shape persisted in the topology file."""
        return {
            "id": self.id,
            "role": self.role,
            "department": self.department,
            "parent": self.parent,
            "skills": list(self.skills),
            "limits": dict(self.limits),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BrainNode":
        """Rebuild a node from a persisted/loaded JSON object.

        Backward-compatible: a node JSON without ``skills``/``limits`` (the
        pre-management shape) loads fine — both default to empty.
        """
        node_id = (data.get("id") or "").strip()
        if not node_id:
            raise TopologyError(f"node is missing an 'id': {data!r}")
        skills_raw = data.get("skills") or ()
        if isinstance(skills_raw, str):
            skills_raw = [skills_raw]
        limits_raw = data.get("limits") or {}
        if not isinstance(limits_raw, dict):
            raise TopologyError(
                f"brain {node_id!r} 'limits' must be a JSON object, got {limits_raw!r}"
            )
        return cls(
            id=node_id,
            role=(data.get("role") or ROLE_GENERAL).strip() or ROLE_GENERAL,
            department=data.get("department") or None,
            parent=data.get("parent") or None,
            skills=tuple(str(s) for s in skills_raw),
            limits=limits_raw,
        )


class Topology:
    """A validated TREE of :class:`BrainNode`\\ s — the org chart.

    Validation (on construction): unique ids, every parent exists, exactly one root
    (``parent is None``), and acyclic. Supports the trivial single-brain topology
    (one root, no children) and arbitrary depth. Node insertion order is preserved
    so iteration / routing is deterministic.
    """

    def __init__(self, nodes: Iterable[BrainNode]) -> None:
        self._nodes: dict[str, BrainNode] = {}
        for node in nodes:
            if node.id in self._nodes:
                raise TopologyError(f"duplicate brain id {node.id!r}")
            self._nodes[node.id] = node
        self._validate()

    # ── validation ───────────────────────────────────────────────────────────
    def _validate(self) -> None:
        if not self._nodes:
            raise TopologyError("an org topology needs at least one brain (got none)")

        roots = [n for n in self._nodes.values() if n.parent is None]
        if len(roots) == 0:
            raise TopologyError("no root brain (every node has a parent → a cycle)")
        if len(roots) > 1:
            raise TopologyError(
                f"exactly one root is required, found {len(roots)}: "
                f"{[n.id for n in roots]}"
            )

        for node in self._nodes.values():
            if node.parent is not None and node.parent not in self._nodes:
                raise TopologyError(
                    f"brain {node.id!r} references a missing parent {node.parent!r}"
                )

        # Acyclic: every node must reach the root by following parents, with no loop.
        for node in self._nodes.values():
            seen: set[str] = set()
            cur: Optional[BrainNode] = node
            while cur is not None and cur.parent is not None:
                if cur.id in seen:
                    raise TopologyError(f"cycle detected at brain {cur.id!r}")
                seen.add(cur.id)
                cur = self._nodes.get(cur.parent)

    # ── queries ──────────────────────────────────────────────────────────────
    def root(self) -> BrainNode:
        """The single root brain (``parent is None``)."""
        for node in self._nodes.values():
            if node.parent is None:
                return node
        raise TopologyError("no root brain")  # pragma: no cover - guarded by validate

    def admin(self) -> BrainNode:
        """The PERMANENT administrator brain — always the root of the chart.

        :func:`load_topology` / :func:`default_topology` guarantee the
        ``config.root_admin_id`` brain (Lhassa) sits at the root, so the
        administrator and the root are the same node ("Lhassa orbits everything").
        """
        return self.root()

    def is_admin(self, node_id: str) -> bool:
        """Whether ``node_id`` is the permanent administrator (i.e. the root)."""
        return node_id == self.root().id

    def node(self, node_id: str) -> BrainNode:
        """The brain with ``node_id``. Raises :class:`KeyError` if absent."""
        try:
            return self._nodes[node_id]
        except KeyError:
            raise KeyError(f"no brain {node_id!r} in this topology") from None

    def has(self, node_id: str) -> bool:
        """Whether ``node_id`` is a brain in this topology."""
        return node_id in self._nodes

    def nodes(self) -> list[BrainNode]:
        """All brains, in insertion order."""
        return list(self._nodes.values())

    def children_of(self, node_id: str) -> list[BrainNode]:
        """The direct children of ``node_id`` (insertion order)."""
        return [n for n in self._nodes.values() if n.parent == node_id]

    def is_leaf(self, node_id: str) -> bool:
        """Whether ``node_id`` has no children (it executes, never coordinates)."""
        return not self.children_of(node_id)

    def descendants(self, node_id: str) -> list[BrainNode]:
        """Every brain under ``node_id`` (depth-first, deterministic order)."""
        out: list[BrainNode] = []
        stack = list(reversed(self.children_of(node_id)))
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(reversed(self.children_of(cur.id)))
        return out

    def is_standalone(self) -> bool:
        """Whether this is the single-brain topology (one root, no children)."""
        return len(self._nodes) == 1

    def to_list(self) -> list[dict]:
        """The JSON node list (insertion order) for persistence/inspection."""
        return [n.to_dict() for n in self._nodes.values()]


# ── default chart derivation ───────────────────────────────────────────────────


def _parse_departments(raw: str) -> list[str]:
    """Parse the CSV ``org_departments`` config into an ordered, de-duped list."""
    seen: list[str] = []
    for item in (raw or "").split(","):
        name = item.strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def admin_id(config: "Optional[Config]" = None) -> str:
    """The id of the PERMANENT root administrator (``config.root_admin_id``).

    Lhassa is the default/main brain — the total administrator that always sits at
    the top of any org and is never removed or demoted. Resolved from config so a
    stand-in without the field still gets the ``"lhassa"`` default.
    """
    config = _resolve_config(config)
    return (getattr(config, "root_admin_id", "") or DEFAULT_ADMIN_ID).strip() or DEFAULT_ADMIN_ID


def _ensure_admin(nodes: Iterable[BrainNode], admin: str) -> list[BrainNode]:
    """Force the permanent administrator to the ROOT of a node set.

    The migration / invariant rule (kept deliberately simple):

      - If the ``admin`` node is missing, one is SYNTHESIZED and prepended.
      - The admin node is pinned to the root (``parent=None``).
      - Any OTHER root (a loaded chart's original root) is re-parented UNDER the
        admin, so the old top-level brain becomes a child of Lhassa.

    The admin is returned first for deterministic iteration; every other node keeps
    its declared order. This makes Lhassa orbit any chart loaded from disk, even an
    old pre-admin one that rooted at ``general``.
    """
    nodes = list(nodes)
    existing = next((n for n in nodes if n.id == admin), None)
    admin_node = (
        replace(existing, parent=None)
        if existing is not None
        else BrainNode(id=admin, role=ROLE_ADMIN)
    )
    others: list[BrainNode] = []
    for n in nodes:
        if n.id == admin:
            continue
        others.append(replace(n, parent=admin) if n.parent is None else n)
    return [admin_node, *others]


def default_topology(config: "Config") -> Topology:
    """Derive a chart from config when no topology file exists.

    The root is ALWAYS the permanent administrator brain ``config.root_admin_id``
    (Lhassa), with ``role="administrator"``:

    - ``org_role`` empty → just the **single admin brain** (Lhassa standalone — it
      both coordinates and executes, there is no one else, but it is still the
      administrator).
    - else → the admin brain with ``[org_departments…]`` hanging directly UNDER it
      (admin → departments).
    """
    admin = admin_id(config)
    role = (getattr(config, "org_role", "") or "").strip().lower()
    if not role:
        return Topology([BrainNode(id=admin, role=ROLE_ADMIN)])

    departments = _parse_departments(getattr(config, "org_departments", "") or "")
    nodes = [BrainNode(id=admin, role=ROLE_ADMIN)]
    for name in departments:
        nodes.append(
            BrainNode(
                id=f"dept:{name}",
                role=ROLE_DEPARTMENT,
                department=name,
                parent=admin,
            )
        )
    return Topology(nodes)


# ── load / save ─────────────────────────────────────────────────────────────────


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def topology_path(config: "Optional[Config]" = None) -> Path:
    """The configured topology file path (``config.org_topology_file``)."""
    config = _resolve_config(config)
    return Path(getattr(config, "org_topology_file", "") or "")


def load_topology(config: "Optional[Config]" = None) -> Topology:
    """Load the org chart: the topology file if present, else a derived default.

    The file is JSON — a list of node objects ``{"id","role","department","parent"}``
    (a bare list, or an object with a ``"nodes"`` key). A missing/empty file falls
    back to :func:`default_topology` (the single admin brain, or the admin with its
    departments), so a fresh install needs no file at all.

    Whatever is loaded, the permanent administrator (``config.root_admin_id``) is
    guaranteed to sit at the ROOT via :func:`_ensure_admin` — a pre-admin chart that
    rooted at ``general`` is migrated so Lhassa orbits above it.
    """
    config = _resolve_config(config)
    path = topology_path(config)
    if path and path.exists():
        raw = json.loads(path.read_text(encoding="utf-8") or "[]")
        items = raw.get("nodes", []) if isinstance(raw, dict) else raw
        nodes = [BrainNode.from_dict(d) for d in items]
        return Topology(_ensure_admin(nodes, admin_id(config)))
    return default_topology(config)


def save_topology(topology: Topology, config: "Optional[Config]" = None) -> Path:
    """Persist ``topology`` to the configured file (creating the parent dir).

    Returns the path written. The file is the user-managed source of truth, so a
    later :func:`load_topology` reflects exactly these nodes.
    """
    config = _resolve_config(config)
    path = topology_path(config)
    if not path:
        raise TopologyError("no org_topology_file configured to save to")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(topology.to_list(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
