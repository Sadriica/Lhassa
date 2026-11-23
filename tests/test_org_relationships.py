"""Tests for lhassa.org.relationships; the typed overlay graph.

Offline: both the topology file and the relationships file are redirected to tmp
paths via a tiny Config stand-in (``_Cfg``), and a small real topology is seeded so
brain-id validation has actual nodes (``lhassa`` is injected at the root on load).
A bad op must NOT corrupt the saved file; that invariant is asserted directly.
"""

import json

import pytest

from lhassa.org import relationships as rel
from lhassa.org import topology as topo
from lhassa.org.relationships import Relationship, RelationshipError
from lhassa.org.topology import BrainNode, Topology


class _Cfg:
    """A minimal Config stand-in with just the org-relevant fields."""

    def __init__(self, *, org_topology_file="", org_relationships_file="", instance_id="inst_local"):
        self.org_role = ""
        self.org_departments = ""
        self.root_admin_id = "lhassa"
        self.org_topology_file = org_topology_file
        self.org_relationships_file = org_relationships_file
        self.instance_id = instance_id


@pytest.fixture
def cfg(tmp_path):
    c = _Cfg(
        org_topology_file=str(tmp_path / "org.json"),
        org_relationships_file=str(tmp_path / "relationships.json"),
    )
    # Seed a small topology so brain-id validation has real nodes. load_topology
    # injects "lhassa" as the permanent root above whatever we save.
    topo.save_topology(
        Topology(
            [
                BrainNode(id="general", role="general"),
                BrainNode(id="dept:a", role="department", department="a", parent="general"),
                BrainNode(id="dept:b", role="department", department="b", parent="general"),
            ]
        ),
        c,
    )
    return c


# ── add / remove each kind ────────────────────────────────────────────────────────


# SHARES is INSTANCE-scoped (its endpoints are whole instances, not brains in
# THIS topology, and its 'to' must equal the local instance id); it does not
# fit the brain-to-brain shape the rest of this file exercises, so it is
# excluded here and covered by its own tests below.
_BRAIN_SCOPED_KINDS = [k for k in rel.RELATION_KINDS if k not in rel.INSTANCE_SCOPED_KINDS]


@pytest.mark.parametrize("kind", _BRAIN_SCOPED_KINDS)
def test_add_then_remove_each_kind(cfg, kind):
    rels = rel.add("dept:a", "dept:b", kind, config=cfg)
    assert Relationship("dept:a", "dept:b", kind) in [
        Relationship(r.from_id, r.to_id, r.kind) for r in rels
    ]
    rel.remove("dept:a", "dept:b", kind, config=cfg)
    assert rel.all(cfg) == []


def test_add_dedupes_same_triple(cfg):
    rel.add("dept:a", "dept:b", "reads", config=cfg, note="first")
    rel.add("dept:a", "dept:b", "reads", config=cfg, note="second")
    rels = rel.all(cfg)
    assert len(rels) == 1


# ── collaborates symmetry ─────────────────────────────────────────────────────────


def test_collaborates_is_symmetric_and_deduped(cfg):
    rel.add("dept:b", "dept:a", "collaborates", config=cfg)
    # Adding the reverse direction must NOT create a second edge (canonical storage).
    rel.add("dept:a", "dept:b", "collaborates", config=cfg)
    rels = rel.all(cfg)
    assert len(rels) == 1
    # Stored canonically (ids sorted): dept:a < dept:b.
    assert (rels[0].from_id, rels[0].to_id) == ("dept:a", "dept:b")


def test_permits_collaborates_both_directions(cfg):
    rel.add("dept:a", "dept:b", "collaborates", config=cfg)
    assert rel.permits("dept:a", "dept:b", "collaborates", cfg) is True
    assert rel.permits("dept:b", "dept:a", "collaborates", cfg) is True


def test_remove_collaborates_either_direction(cfg):
    rel.add("dept:a", "dept:b", "collaborates", config=cfg)
    rel.remove("dept:b", "dept:a", "collaborates", config=cfg)  # reverse order
    assert rel.all(cfg) == []


# ── directed kinds honour direction ───────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["reads", "notifies", "reviews"])
def test_directed_permits_one_way(cfg, kind):
    rel.add("dept:a", "dept:b", kind, config=cfg)
    assert rel.permits("dept:a", "dept:b", kind, cfg) is True
    assert rel.permits("dept:b", "dept:a", kind, cfg) is False


def test_permits_unknown_kind_is_false_not_raise(cfg):
    assert rel.permits("dept:a", "dept:b", "bogus", cfg) is False


def test_permits_absent_edge_is_false(cfg):
    assert rel.permits("dept:a", "dept:b", "reads", cfg) is False


# ── list_for ──────────────────────────────────────────────────────────────────────


def test_list_for_splits_outgoing_incoming(cfg):
    rel.add("dept:a", "dept:b", "reads", config=cfg)      # a outgoing, b incoming
    rel.add("general", "dept:a", "notifies", config=cfg)  # a incoming
    view = rel.list_for("dept:a", cfg)
    out_pairs = {(e["from"], e["to"], e["kind"]) for e in view["outgoing"]}
    in_pairs = {(e["from"], e["to"], e["kind"]) for e in view["incoming"]}
    assert ("dept:a", "dept:b", "reads") in out_pairs
    assert ("general", "dept:a", "notifies") in in_pairs
    assert ("dept:a", "dept:b", "reads") not in in_pairs


def test_list_for_collaborates_in_both_buckets(cfg):
    rel.add("dept:a", "dept:b", "collaborates", config=cfg)
    for brain in ("dept:a", "dept:b"):
        view = rel.list_for(brain, cfg)
        assert len(view["outgoing"]) == 1
        assert len(view["incoming"]) == 1


# ── rejections ────────────────────────────────────────────────────────────────────


def test_reject_self_edge(cfg):
    with pytest.raises(RelationshipError):
        rel.add("dept:a", "dept:a", "reads", config=cfg)


def test_reject_unknown_kind(cfg):
    with pytest.raises(RelationshipError):
        rel.add("dept:a", "dept:b", "bogus", config=cfg)


def test_reject_nonexistent_brain(cfg):
    with pytest.raises(RelationshipError):
        rel.add("dept:a", "dept:ghost", "reads", config=cfg)
    with pytest.raises(RelationshipError):
        rel.add("ghost", "dept:b", "reads", config=cfg)


# ── JSON round-trip + no-corruption ───────────────────────────────────────────────


def test_round_trips_json_file(cfg):
    rel.add("dept:a", "dept:b", "reads", config=cfg, note="why")
    rel.add("dept:a", "dept:b", "collaborates", config=cfg)
    reloaded = rel.load(cfg)
    assert len(reloaded) == 2
    raw = json.loads((open(cfg.org_relationships_file).read()))
    assert isinstance(raw, list)
    assert {r["kind"] for r in raw} == {"reads", "collaborates"}
    note_edge = next(r for r in reloaded if r.kind == "reads")
    assert note_edge.note == "why"


def test_bad_op_does_not_corrupt_file(cfg):
    rel.add("dept:a", "dept:b", "reads", config=cfg)
    before = open(cfg.org_relationships_file).read()
    with pytest.raises(RelationshipError):
        rel.add("dept:a", "dept:a", "reads", config=cfg)       # self-edge
    with pytest.raises(RelationshipError):
        rel.add("dept:a", "ghost", "collaborates", config=cfg)  # missing brain
    after = open(cfg.org_relationships_file).read()
    assert before == after
    assert len(rel.all(cfg)) == 1


def test_load_skips_malformed_entries(cfg):
    open(cfg.org_relationships_file, "w").write(
        json.dumps(
            [
                {"from": "dept:a", "to": "dept:b", "kind": "reads"},
                {"from": "dept:a", "to": "dept:b", "kind": "bogus"},  # bad kind
                {"from": "", "to": "dept:b", "kind": "reads"},         # blank id
                "not-an-object",
            ]
        )
    )
    rels = rel.load(cfg)
    assert len(rels) == 1
    assert rels[0].kind == "reads"


def test_describe_shape(cfg):
    rel.add("dept:a", "dept:b", "reviews", config=cfg, note="approve gate")
    desc = rel.describe(cfg)
    assert desc["kinds"] == list(rel.RELATION_KINDS)
    assert desc["relationships"] == [
        {"from": "dept:a", "to": "dept:b", "kind": "reviews", "note": "approve gate"}
    ]


# ── SHARES; the instance-scoped cross-instance kind (Stream 6 Phase A) ───────


def test_shares_add_requires_to_equal_local_instance(cfg):
    # cfg.instance_id == "inst_local"; granting FROM a foreign instance TO the
    # local one is valid, the reverse is refused (an instance can only grant
    # access to ITSELF, never impersonate a grant for someone else).
    rel.add("inst_remote", "inst_local", rel.SHARES, config=cfg)
    assert rel.permits("inst_remote", "inst_local", rel.SHARES, cfg) is True
    with pytest.raises(RelationshipError):
        rel.add("inst_local", "inst_remote", rel.SHARES, config=cfg)


def test_shares_endpoints_need_no_local_topology_membership(cfg):
    # Neither "inst_remote" nor "inst_local" is a node in cfg's org topology
    # (only "general"/"dept:a"/"dept:b" are); SHARES must not require that.
    rel.add("some-other-instance", "inst_local", rel.SHARES, config=cfg)
    assert rel.permits("some-other-instance", "inst_local", rel.SHARES, cfg) is True


def test_shares_permits_denies_by_default(cfg):
    assert rel.permits("inst_remote", "inst_local", rel.SHARES, cfg) is False


def test_shares_is_not_symmetric(cfg):
    rel.add("inst_remote", "inst_local", rel.SHARES, config=cfg)
    assert rel.permits("inst_local", "inst_remote", rel.SHARES, cfg) is False


def test_shares_remove_revokes(cfg):
    rel.add("inst_remote", "inst_local", rel.SHARES, config=cfg)
    rel.remove("inst_remote", "inst_local", rel.SHARES, config=cfg)
    assert rel.permits("inst_remote", "inst_local", rel.SHARES, cfg) is False
