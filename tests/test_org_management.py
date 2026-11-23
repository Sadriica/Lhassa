"""Tests for lhassa.org.management; create brains, skills, limits, hierarchy.

Offline: the topology file is redirected to a tmp path via a tiny Config stand-in
(``_Cfg``), so no real config/DB is touched. Every mutation is validated through a
``Topology`` construction before it is persisted, so a bad op must NOT corrupt the
saved file; that invariant is asserted directly.
"""

import json

import pytest

from lhassa.org import management
from lhassa.org import topology as topo
from lhassa.org.management import ManagementError
from lhassa.org.topology import BrainNode, Topology, TopologyError


class _Cfg:
    """A minimal Config stand-in with just the topology-relevant fields."""

    def __init__(self, *, org_role="general", org_departments="", org_topology_file=""):
        self.org_role = org_role
        self.org_departments = org_departments
        self.org_topology_file = org_topology_file


def _cfg(tmp_path):
    return _Cfg(org_topology_file=str(tmp_path / "org.json"))


def _seed_flat(cfg):
    Topology(
        [
            BrainNode(id="general", role="general"),
            BrainNode(id="dept:optimization", role="department",
                      department="optimization", parent="general"),
            BrainNode(id="dept:finance", role="department",
                      department="finance", parent="general"),
        ]
    )
    topo.save_topology(
        Topology(
            [
                BrainNode(id="general", role="general"),
                BrainNode(id="dept:optimization", role="department",
                          department="optimization", parent="general"),
                BrainNode(id="dept:finance", role="department",
                          department="finance", parent="general"),
            ]
        ),
        cfg,
    )


# ── node model round-trips skills + limits; old JSON loads ──────────────────────────


def test_node_round_trips_skills_and_limits(tmp_path):
    cfg = _cfg(tmp_path)
    node = BrainNode(
        id="dept:opt", role="department", department="optimization",
        parent="general", skills=["optimization", "extra"],
        limits={"max_risk": "low", "budget_usd": 1.5, "allowed_actions": ["read"]},
    )
    topo.save_topology(Topology([BrainNode(id="general"), node]), cfg)
    reloaded = topo.load_topology(cfg)
    got = reloaded.node("dept:opt")
    assert got.skills == ("optimization", "extra")
    assert got.limits == {"max_risk": "low", "budget_usd": 1.5,
                          "allowed_actions": ["read"]}


def test_old_json_without_skills_limits_loads(tmp_path):
    path = tmp_path / "org.json"
    path.write_text(json.dumps([
        {"id": "general", "role": "general", "department": None, "parent": None},
        {"id": "dept:x", "role": "department", "department": "x", "parent": "general"},
    ]))
    cfg = _Cfg(org_topology_file=str(path))
    t = topo.load_topology(cfg)
    assert t.node("dept:x").skills == ()
    assert t.node("dept:x").limits == {}


def test_node_is_still_hashable_with_limits():
    # limits is excluded from the hash, so a frozen node stays hashable.
    node = BrainNode(id="a", skills=("s",), limits={"budget_usd": 1.0})
    assert hash(node) == hash(BrainNode(id="a", skills=("s",), limits={"other": 9}))


# ── add / update / remove ───────────────────────────────────────────────────────────


def test_add_brain_persists(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    updated = management.add_brain(
        "dept:doc", role="department", parent="general",
        department="documentation", skills=["optimization"],
        limits={"budget_usd": 2.0}, config=cfg,
    )
    assert updated.has("dept:doc")
    reloaded = topo.load_topology(cfg)
    n = reloaded.node("dept:doc")
    assert n.parent == "general"
    assert n.skills == ("optimization",)
    assert n.limits == {"budget_usd": 2.0}


def test_add_duplicate_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    with pytest.raises(ManagementError):
        management.add_brain("general", role="general", config=cfg)


def test_add_missing_parent_rejected_does_not_corrupt(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    before = (tmp_path / "org.json").read_text()
    with pytest.raises(TopologyError):
        management.add_brain("dept:y", parent="ghost", config=cfg)
    # the file is byte-for-byte unchanged; the op never persisted.
    assert (tmp_path / "org.json").read_text() == before


def test_update_brain_partial(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    management.update_brain("dept:finance", role="lead", config=cfg)
    n = topo.load_topology(cfg).node("dept:finance")
    assert n.role == "lead"
    assert n.department == "finance"  # untouched


def test_set_skills_and_set_limits(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    management.set_skills("dept:optimization", ["optimization", "speed"], config=cfg)
    management.set_limits("dept:optimization", {"max_risk": "medium"}, config=cfg)
    n = topo.load_topology(cfg).node("dept:optimization")
    assert n.skills == ("optimization", "speed")
    assert n.limits == {"max_risk": "medium"}


def test_remove_leaf(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    updated = management.remove_brain("dept:finance", config=cfg)
    assert not updated.has("dept:finance")


def test_remove_with_children_refused(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    before = (tmp_path / "org.json").read_text()
    with pytest.raises(ManagementError):
        management.remove_brain("general", config=cfg)
    assert (tmp_path / "org.json").read_text() == before


def test_remove_subtree_with_force(tmp_path):
    cfg = _cfg(tmp_path)
    topo.save_topology(
        Topology([
            BrainNode(id="general", role="general"),
            BrainNode(id="ops", role="sub_general", parent="general"),
            BrainNode(id="dept:opt", role="department",
                      department="optimization", parent="ops"),
        ]),
        cfg,
    )
    updated = management.remove_brain("ops", force=True, config=cfg)
    # the permanent administrator (Lhassa) is injected above the loaded 'general'.
    assert {n.id for n in updated.nodes()} == {"lhassa", "general"}


# ── set_parent (re-parent); who commands whom ──────────────────────────────────────


def test_set_parent_reparents(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    management.set_parent("dept:finance", "dept:optimization", config=cfg)
    assert topo.load_topology(cfg).node("dept:finance").parent == "dept:optimization"


def test_set_parent_self_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    with pytest.raises(ManagementError):
        management.set_parent("dept:finance", "dept:finance", config=cfg)


def test_set_parent_cycle_rejected_does_not_corrupt(tmp_path):
    cfg = _cfg(tmp_path)
    topo.save_topology(
        Topology([
            BrainNode(id="general", role="general"),
            BrainNode(id="ops", role="sub_general", parent="general"),
            BrainNode(id="dept:opt", role="department",
                      department="optimization", parent="ops"),
        ]),
        cfg,
    )
    before = (tmp_path / "org.json").read_text()
    # moving 'ops' under its own descendant would create a cycle → rejected
    with pytest.raises(TopologyError):
        management.set_parent("ops", "dept:opt", config=cfg)
    assert (tmp_path / "org.json").read_text() == before


# ── set_root (PROMOTE); a top-level coordinator UNDER the permanent admin ───────────
# Lhassa is never unseated: promoting B makes B a direct child of the administrator.


def test_promote_makes_top_level_child_of_admin(tmp_path):
    cfg = _cfg(tmp_path)
    topo.save_topology(
        Topology([
            BrainNode(id="general", role="general"),
            BrainNode(id="ops", role="sub_general", parent="general"),
            BrainNode(id="dept:opt", role="department",
                      department="optimization", parent="ops"),
        ]),
        cfg,
    )
    updated = management.set_root("dept:opt", config=cfg)
    # Lhassa stays the absolute root; dept:opt becomes a direct child of the admin.
    assert updated.root().id == "lhassa"
    assert updated.node("dept:opt").parent == "lhassa"
    # the rest of the chart is preserved (general still under lhassa, ops under general)
    assert updated.node("general").parent == "lhassa"
    assert updated.node("ops").parent == "general"


def test_promote_admin_itself_is_noop(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    updated = management.set_root("lhassa", config=cfg)
    assert updated.root().id == "lhassa"


def test_promote_already_top_level_is_noop(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)  # general becomes a direct child of lhassa on load
    updated = management.set_root("general", config=cfg)
    assert updated.root().id == "lhassa"
    assert updated.node("general").parent == "lhassa"


def test_promote_preserves_offspine_subtrees(tmp_path):
    cfg = _cfg(tmp_path)
    # general → [biz → fin], [ops → opt]. Promote 'opt' to top-level under the admin.
    # 'fin' (off the promoted brain) must keep its parent 'biz'.
    topo.save_topology(
        Topology([
            BrainNode(id="general", role="general"),
            BrainNode(id="biz", role="sub_general", parent="general"),
            BrainNode(id="ops", role="sub_general", parent="general"),
            BrainNode(id="fin", role="department", department="finance", parent="biz"),
            BrainNode(id="opt", role="department", department="optimization", parent="ops"),
        ]),
        cfg,
    )
    updated = management.set_root("opt", config=cfg)
    assert updated.root().id == "lhassa"
    assert updated.node("opt").parent == "lhassa"   # now a top-level coordinator
    assert updated.node("fin").parent == "biz"      # untouched subtree intact
    assert updated.node("biz").parent == "general"


# ── permanent administrator invariant (Lhassa is never removed/demoted) ──────────────


def test_remove_admin_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    before = (tmp_path / "org.json").read_text()
    with pytest.raises(ManagementError):
        management.remove_brain("lhassa", config=cfg)
    assert (tmp_path / "org.json").read_text() == before


def test_move_admin_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    with pytest.raises(ManagementError):
        management.set_parent("lhassa", "general", config=cfg)


def test_add_with_no_parent_attaches_under_admin(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    updated = management.add_brain("dept:doc", role="department", config=cfg)
    assert updated.node("dept:doc").parent == "lhassa"


def test_add_second_admin_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    with pytest.raises(ManagementError):
        management.add_brain("lhassa", role="administrator", config=cfg)


# ── describe ────────────────────────────────────────────────────────────────────────


def test_describe_shape(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    management.set_skills("dept:optimization", ["optimization"], config=cfg)
    desc = management.describe(cfg)
    # the permanent administrator (Lhassa) is the root and is exposed as "admin".
    assert desc["root"] == "lhassa"
    assert desc["admin"] == "lhassa"
    assert desc["standalone"] is False
    ids = {n["id"] for n in desc["nodes"]}
    assert ids == {"lhassa", "general", "dept:optimization", "dept:finance"}
    lhassa = next(n for n in desc["nodes"] if n["id"] == "lhassa")
    assert lhassa["is_admin"] is True
    general = next(n for n in desc["nodes"] if n["id"] == "general")
    assert general["is_admin"] is False
    assert set(general["children"]) == {"dept:optimization", "dept:finance"}
    assert general["is_leaf"] is False
    opt = next(n for n in desc["nodes"] if n["id"] == "dept:optimization")
    assert opt["skills"] == ["optimization"]
    assert opt["is_leaf"] is True


def test_describe_standalone_default(tmp_path):
    cfg = _Cfg(org_role="", org_topology_file=str(tmp_path / "missing.json"))
    desc = management.describe(cfg)
    assert desc["standalone"] is True
    assert desc["root"] == "lhassa"
    assert desc["admin"] == "lhassa"


# ── available skills (what CAN be assigned) ─────────────────────────────────────────


def test_available_skills_lists_registry_domains():
    skills = management.available_skills()
    domains = {s["domain"] for s in skills}
    assert "optimization" in domains


def test_validate_skills_warns_but_keeps_unknown():
    cleaned, unknown = management.validate_skills(["optimization", "totally_new"])
    assert cleaned == ("optimization", "totally_new")
    assert unknown == ["totally_new"]
