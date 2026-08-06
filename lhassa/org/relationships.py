# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed org RELATIONSHIPS — the overlay graph (Fase 7).

The org chart is a directed GRAPH: the command TREE (who-commands-whom, in
``org.json`` via :mod:`lhassa.org.topology`, with Lhassa the sovereign root) PLUS
an OVERLAY of typed RELATIONSHIP edges that are NOT hierarchy. This module is that
overlay — a separate graph stored at ``config.org_relationships_file`` so the
command tree and the relationship edges never tangle.

The first closed set of edge kinds (:data:`RELATION_KINDS`), each reusing existing
primitives so it has REAL meaning:

  - ``reads``        — A may read B's state/output/memory-scope (directed A→B).
  - ``notifies``     — A pushes info/events to B (directed A→B).
  - ``reviews``      — B reviews/approves A's work (directed A→B: A is reviewed BY B).
  - ``collaborates`` — peers share context, BIDIRECTIONAL (symmetric A↔B).

``collaborates`` is symmetric: it is stored CANONICALLY (the two ids sorted) so
A↔B is never duplicated as B↔A, and :func:`permits` honours it in both directions.
The other three are directed: :func:`permits` only answers true for the exact
stored direction.

This slice is the DATA MODEL + ops + API. Runtime wiring comes later; the seam for
it is :func:`permits` ("may A read B?"), a pure read callers can ask without
touching the rest of the brain.

Every mutating op VALIDATES (both ids are real topology nodes, the kind is known,
no self-edge) BEFORE it writes, so a bad op raises a clear :class:`ValueError` and
NEVER corrupts the on-disk file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from lhassa.org import topology as topo

if TYPE_CHECKING:
    from lhassa.config import Config

# ── the closed edge-kind vocabulary ─────────────────────────────────────────────
READS = "reads"
NOTIFIES = "notifies"
REVIEWS = "reviews"
COLLABORATES = "collaborates"
# SHARES — cross-INSTANCE, not cross-brain (Stream 6 Phase A). Edge (A, B,
# shares) means "instance A may discover/subscribe to instance B's published
# resources" — the SAME directed-A-reads-B shape as READS, one level up (whole
# brains, not sub-brains within one tree). See INSTANCE_SCOPED_KINDS below: this
# is the one kind whose endpoints are NOT required to be local topology nodes.
SHARES = "shares"

# The closed set of valid relationship kinds. Adding a kind is a deliberate edit
# here (and a description below) — the set is closed on purpose.
RELATION_KINDS: tuple[str, ...] = (READS, NOTIFIES, REVIEWS, COLLABORATES, SHARES)

# Which kinds are SYMMETRIC (bidirectional). A symmetric edge is stored canonically
# (ids sorted) so A↔B is never duplicated, and ``permits`` answers true both ways.
SYMMETRIC_KINDS: frozenset[str] = frozenset({COLLABORATES})

# Which kinds are INSTANCE-scoped rather than brain-scoped: their endpoints are
# whole Lhassa instances (possibly foreign — never in this instance's own org
# topology), so the local-topology membership check (_require_brains) does not
# apply. Deliberately a SEPARATE closed set from RELATION_KINDS: extending
# cross-instance semantics to a kind is a distinct decision from adding the kind
# at all. For a SHARES edge specifically, ``to_id`` MUST be this instance's own
# identity (Config.instance_id) — only the resource owner can grant access to
# ITSELF; ``from_id`` is the (foreign, unvalidated) requester.
INSTANCE_SCOPED_KINDS: frozenset[str] = frozenset({SHARES})

# One-line semantics for the dashboard legend / the kinds API endpoint.
KIND_DESCRIPTIONS: dict[str, str] = {
    READS: "A may read B's state/output/memory-scope (directed A→B).",
    NOTIFIES: "A pushes info/events to B (directed A→B).",
    REVIEWS: "B reviews/approves A's work (directed A→B; A is reviewed by B).",
    COLLABORATES: "Peers share context, bidirectional (symmetric A↔B).",
    SHARES: "Instance A may discover/subscribe to instance B's shared "
    "resources (directed A→B; B is this instance, the grantor).",
}


class RelationshipError(ValueError):
    """Raised on an invalid relationship op (unknown kind, self-edge, missing brain).

    A :class:`ValueError` subclass so the API/CLI can catch it with one
    ``except ValueError`` and map a bad op to a clear 4xx, never touching the file.
    """


@dataclass(frozen=True)
class Relationship:
    """One typed overlay edge (immutable, hashable).

    Identity is the ``(from_id, to_id, kind)`` triple — ``note``/``scope`` are
    metadata excluded from equality/hash, so two edges with the same triple but
    different notes dedupe to one. For a symmetric ``kind`` the triple is stored
    canonically (ids sorted) by :func:`add`, so A↔B and B↔A are the same edge.
    """

    from_id: str
    to_id: str
    kind: str
    note: str = field(default="", compare=False)
    scope: str = field(default="", compare=False)

    def to_dict(self) -> dict:
        """Serialise to the JSON edge shape (``from``/``to``/``kind``/``note``/``scope``)."""
        return {
            "from": self.from_id,
            "to": self.to_id,
            "kind": self.kind,
            "note": self.note,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Relationship":
        """Rebuild an edge from a persisted JSON object. Accepts ``from_id``/``from``."""
        return cls(
            from_id=str(data.get("from") or data.get("from_id") or "").strip(),
            to_id=str(data.get("to") or data.get("to_id") or "").strip(),
            kind=str(data.get("kind") or "").strip(),
            note=str(data.get("note") or ""),
            scope=str(data.get("scope") or ""),
        )


# ── helpers ─────────────────────────────────────────────────────────────────────


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def relationships_path(config: "Optional[Config]" = None) -> Path:
    """The configured relationships file path (``config.org_relationships_file``)."""
    config = _resolve_config(config)
    return Path(getattr(config, "org_relationships_file", "") or "")


def _canonical(from_id: str, to_id: str, kind: str) -> tuple[str, str]:
    """Order the endpoints for storage.

    A symmetric kind is stored with the two ids SORTED so A↔B and B↔A collapse to a
    single canonical edge; a directed kind keeps the given order.
    """
    if kind in SYMMETRIC_KINDS and to_id < from_id:
        return to_id, from_id
    return from_id, to_id


# ── load / save ───────────────────────────────────────────────────────────────────


def load(config: "Optional[Config]" = None) -> list[Relationship]:
    """Load the overlay edges from the relationships file (``[]`` if missing).

    Tolerant: a missing/empty file yields no edges; malformed entries (unknown kind,
    blank endpoint) are skipped rather than raising, so a hand-edited file can never
    crash a reader. Duplicate triples are collapsed (insertion order preserved).
    """
    path = relationships_path(config)
    if not path or not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    items = raw.get("relationships", []) if isinstance(raw, dict) else raw
    out: list[Relationship] = []
    seen: set[Relationship] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        rel = Relationship.from_dict(item)
        if not rel.from_id or not rel.to_id or rel.kind not in RELATION_KINDS:
            continue
        f, t = _canonical(rel.from_id, rel.to_id, rel.kind)
        rel = Relationship(f, t, rel.kind, note=rel.note, scope=rel.scope)
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
    return out


def save(rels: list[Relationship], config: "Optional[Config]" = None) -> Path:
    """Persist ``rels`` (a JSON list) to the configured file, creating its dir."""
    path = relationships_path(config)
    if not path:
        raise RelationshipError("no org_relationships_file configured to save to")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([r.to_dict() for r in rels], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


# ── mutate ────────────────────────────────────────────────────────────────────────


def _validate_kind(kind: str) -> str:
    kind = (kind or "").strip()
    if kind not in RELATION_KINDS:
        raise RelationshipError(
            f"unknown relationship kind {kind!r}; valid kinds: {', '.join(RELATION_KINDS)}"
        )
    return kind


def _require_brains(from_id: str, to_id: str, config: "Optional[Config]") -> None:
    """Both endpoints must be real nodes in the command tree (topology)."""
    topology = topo.load_topology(config)
    for bid in (from_id, to_id):
        if not topology.has(bid):
            raise RelationshipError(f"no brain {bid!r} in the org topology")


def _require_instance_grant(from_id: str, to_id: str, config: "Optional[Config]") -> None:
    """Validate an INSTANCE-scoped edge (currently only :data:`SHARES`).

    Endpoints are whole instances, not local topology brains — a foreign
    ``from_id`` is expected and NOT checked against the local tree. What IS
    enforced: ``to_id`` must be THIS instance's own id, so an instance can only
    ever grant access to its own resources, never impersonate a grant on
    another instance's behalf.
    """
    cfg = _resolve_config(config)
    local_id = str(getattr(cfg, "instance_id", "") or "").strip()
    if not local_id or to_id != local_id:
        raise RelationshipError(
            "an instance-scoped edge's 'to' must be this instance's own id "
            f"({local_id!r}); an instance can only grant access to itself"
        )


def add(
    from_id: str,
    to_id: str,
    kind: str,
    *,
    config: "Optional[Config]" = None,
    note: str = "",
    scope: str = "",
) -> list[Relationship]:
    """Add a typed edge and persist it. Returns the full updated edge list.

    Validates (kind is known, both brains exist, no self-edge) BEFORE writing, so a
    bad op raises and never corrupts the file. A symmetric ``collaborates`` edge is
    stored canonically (ids sorted) so A↔B isn't duplicated; an already-present
    triple is a no-op (the note/scope of the existing edge is kept).
    """
    kind = _validate_kind(kind)
    from_id = (from_id or "").strip()
    to_id = (to_id or "").strip()
    if not from_id or not to_id:
        raise RelationshipError("a relationship needs a non-empty 'from' and 'to'")
    if from_id == to_id:
        raise RelationshipError(f"a brain cannot have a {kind!r} edge to itself")
    if kind in INSTANCE_SCOPED_KINDS:
        _require_instance_grant(from_id, to_id, config)
    else:
        _require_brains(from_id, to_id, config)

    from_id, to_id = _canonical(from_id, to_id, kind)
    new = Relationship(from_id, to_id, kind, note=note or "", scope=scope or "")
    rels = load(config)
    if new in rels:  # same triple already present (note/scope excluded from identity)
        return rels
    rels.append(new)
    save(rels, config)
    return rels


def remove(
    from_id: str,
    to_id: str,
    kind: str,
    *,
    config: "Optional[Config]" = None,
) -> list[Relationship]:
    """Remove a typed edge and persist. Returns the updated list (idempotent).

    Honours symmetry: removing a ``collaborates`` edge matches it in either
    direction. Removing an absent edge is a no-op (no error, no rewrite of nothing).
    """
    kind = _validate_kind(kind)
    from_id = (from_id or "").strip()
    to_id = (to_id or "").strip()
    from_id, to_id = _canonical(from_id, to_id, kind)
    target = Relationship(from_id, to_id, kind)
    rels = load(config)
    remaining = [r for r in rels if r != target]
    if len(remaining) != len(rels):
        save(remaining, config)
    return remaining


# ── query ─────────────────────────────────────────────────────────────────────────


def all(config: "Optional[Config]" = None) -> list[Relationship]:  # noqa: A001 - the overlay's "all edges"
    """Every overlay edge (alias of :func:`load`, the read seam for callers)."""
    return load(config)


def list_for(brain_id: str, config: "Optional[Config]" = None) -> dict:
    """The edges touching ``brain_id`` split into ``{outgoing, incoming}``.

    Directed edges land in exactly one bucket (``outgoing`` when ``brain_id`` is the
    source, ``incoming`` when it is the target). A SYMMETRIC ``collaborates`` edge is
    bidirectional, so when ``brain_id`` participates it appears in BOTH buckets (the
    peer is on the other side either way) — the truthful view of a two-way edge.
    """
    brain_id = (brain_id or "").strip()
    outgoing: list[dict] = []
    incoming: list[dict] = []
    for rel in load(config):
        symmetric = rel.kind in SYMMETRIC_KINDS
        participant = brain_id in (rel.from_id, rel.to_id)
        if rel.from_id == brain_id or (symmetric and participant):
            outgoing.append(rel.to_dict())
        if rel.to_id == brain_id or (symmetric and participant):
            incoming.append(rel.to_dict())
    return {"outgoing": outgoing, "incoming": incoming}


def permits(
    from_id: str,
    to_id: str,
    kind: str,
    config: "Optional[Config]" = None,
) -> bool:
    """Does an edge of ``kind`` exist from A to B? The runtime seam ("may A read B?").

    Pure read — never raises (an unknown kind simply returns ``False``). Honours
    direction: ``reads``/``notifies``/``reviews`` answer true ONLY for the exact
    stored direction A→B, while the symmetric ``collaborates`` answers true for A→B
    AND B→A.
    """
    kind = (kind or "").strip()
    if kind not in RELATION_KINDS:
        return False
    from_id = (from_id or "").strip()
    to_id = (to_id or "").strip()
    for rel in load(config):
        if rel.kind != kind:
            continue
        if rel.from_id == from_id and rel.to_id == to_id:
            return True
        if kind in SYMMETRIC_KINDS and rel.from_id == to_id and rel.to_id == from_id:
            return True
    return False


# ── inspect ───────────────────────────────────────────────────────────────────────


def describe(config: "Optional[Config]" = None) -> dict:
    """The overlay as a serialisable dict for the dashboard canvas.

    Shape::

        {
          "relationships": [{"from","to","kind","note"}, ...],
          "kinds": ["reads", "notifies", "reviews", "collaborates"]
        }

    The canvas draws each entry as a typed overlay edge on top of the command tree;
    ``kinds`` is the closed vocabulary for the legend.
    """
    return {
        "relationships": [
            {"from": r.from_id, "to": r.to_id, "kind": r.kind, "note": r.note}
            for r in load(config)
        ],
        "kinds": list(RELATION_KINDS),
    }
