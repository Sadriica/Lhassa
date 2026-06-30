"""Tests for lhassa.org.management — create brains, skills, limits, hierarchy.

Offline: the topology file is redirected to a tmp path via a tiny Config stand-in
(``_Cfg``), so no real config/DB is touched. Every mutation is validated through a
``Topology`` construction before it is persisted, so a bad op must NOT corrupt the
saved file — that invariant is asserted directly.
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
            BrainNode(id="dept:optimizacion", role="department",
                      department="optimizacion", parent="general"),
            BrainNode(id="dept:finanzas", role="department",
                      department="finanzas", parent="general"),
        ]
    )
    topo.save_topology(
        Topology(
            [
                BrainNode(id="general", role="general"),
                BrainNode(id="dept:optimizacion", role="department",
                          department="optimizacion", parent="general"),
                BrainNode(id="dept:finanzas", role="department",
                          department="finanzas", parent="general"),
            ]
        ),
        cfg,
    )


# ── node model round-trips skills + limits; old JSON loads ──────────────────────────


def test_node_round_trips_skills_and_limits(tmp_path):
    cfg = _cfg(tmp_path)
    node = BrainNode(
        id="dept:opt", role="department", department="optimizacion",
        parent="general", skills=["optimizacion", "extra"],
        limits={"max_risk": "low", "budget_usd": 1.5, "allowed_actions": ["read"]},
    )
    topo.save_topology(Topology([BrainNode(id="general"), node]), cfg)
    reloaded = topo.load_topology(cfg)
    got = reloaded.node("dept:opt")
    assert got.skills == ("optimizacion", "extra")
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
        department="documentacion", skills=["optimizacion"],
        limits={"budget_usd": 2.0}, config=cfg,
    )
    assert updated.has("dept:doc")
    reloaded = topo.load_topology(cfg)
    n = reloaded.node("dept:doc")
    assert n.parent == "general"
    assert n.skills == ("optimizacion",)
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
    # the file is byte-for-byte unchanged — the op never persisted.
    assert (tmp_path / "org.json").read_text() == before


def test_update_brain_partial(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    management.update_brain("dept:finanzas", role="lead", config=cfg)
    n = topo.load_topology(cfg).node("dept:finanzas")
    assert n.role == "lead"
    assert n.department == "finanzas"  # untouched


def test_set_skills_and_set_limits(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    management.set_skills("dept:optimizacion", ["optimizacion", "speed"], config=cfg)
    management.set_limits("dept:optimizacion", {"max_risk": "medium"}, config=cfg)
    n = topo.load_topology(cfg).node("dept:optimizacion")
    assert n.skills == ("optimizacion", "speed")
    assert n.limits == {"max_risk": "medium"}


def test_remove_leaf(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    updated = management.remove_brain("dept:finanzas", config=cfg)
    assert not updated.has("dept:finanzas")


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
                      department="optimizacion", parent="ops"),
        ]),
        cfg,
    )
    updated = management.remove_brain("ops", force=True, config=cfg)
    assert {n.id for n in updated.nodes()} == {"general"}


# ── set_parent (re-parent) — who commands whom ──────────────────────────────────────


def test_set_parent_reparents(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    management.set_parent("dept:finanzas", "dept:optimizacion", config=cfg)
    assert topo.load_topology(cfg).node("dept:finanzas").parent == "dept:optimizacion"


def test_set_parent_self_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    with pytest.raises(ManagementError):
        management.set_parent("dept:finanzas", "dept:finanzas", config=cfg)


def test_set_parent_cycle_rejected_does_not_corrupt(tmp_path):
    cfg = _cfg(tmp_path)
    topo.save_topology(
        Topology([
            BrainNode(id="general", role="general"),
            BrainNode(id="ops", role="sub_general", parent="general"),
            BrainNode(id="dept:opt", role="department",
                      department="optimizacion", parent="ops"),
        ]),
        cfg,
    )
    before = (tmp_path / "org.json").read_text()
    # moving 'ops' under its own descendant would create a cycle → rejected
    with pytest.raises(TopologyError):
        management.set_parent("ops", "dept:opt", config=cfg)
    assert (tmp_path / "org.json").read_text() == before


# ── set_root (re-root) — who commands everyone ──────────────────────────────────────


def test_set_root_reroots_along_spine(tmp_path):
    cfg = _cfg(tmp_path)
    topo.save_topology(
        Topology([
            BrainNode(id="general", role="general"),
            BrainNode(id="ops", role="sub_general", parent="general"),
            BrainNode(id="dept:opt", role="department",
                      department="optimizacion", parent="ops"),
        ]),
        cfg,
    )
    updated = management.set_root("dept:opt", config=cfg)
    # the promoted brain is the new root …
    assert updated.root().id == "dept:opt"
    # … and the spine flipped: ops→child of dept:opt, general→child of ops.
    assert updated.node("dept:opt").parent is None
    assert updated.node("ops").parent == "dept:opt"
    assert updated.node("general").parent == "ops"


def test_set_root_already_root_is_noop(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    updated = management.set_root("general", config=cfg)
    assert updated.root().id == "general"


def test_set_root_preserves_offspine_subtrees(tmp_path):
    cfg = _cfg(tmp_path)
    # general → [biz → fin], [ops → opt]. Promote 'opt'. 'fin' (off the spine)
    # must keep its parent 'biz'.
    topo.save_topology(
        Topology([
            BrainNode(id="general", role="general"),
            BrainNode(id="biz", role="sub_general", parent="general"),
            BrainNode(id="ops", role="sub_general", parent="general"),
            BrainNode(id="fin", role="department", department="finanzas", parent="biz"),
            BrainNode(id="opt", role="department", department="optimizacion", parent="ops"),
        ]),
        cfg,
    )
    updated = management.set_root("opt", config=cfg)
    assert updated.root().id == "opt"
    assert updated.node("fin").parent == "biz"  # off-spine subtree intact
    assert updated.node("biz").parent == "general"


# ── describe ────────────────────────────────────────────────────────────────────────


def test_describe_shape(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_flat(cfg)
    management.set_skills("dept:optimizacion", ["optimizacion"], config=cfg)
    desc = management.describe(cfg)
    assert desc["root"] == "general"
    assert desc["standalone"] is False
    ids = {n["id"] for n in desc["nodes"]}
    assert ids == {"general", "dept:optimizacion", "dept:finanzas"}
    general = next(n for n in desc["nodes"] if n["id"] == "general")
    assert set(general["children"]) == {"dept:optimizacion", "dept:finanzas"}
    assert general["is_leaf"] is False
    opt = next(n for n in desc["nodes"] if n["id"] == "dept:optimizacion")
    assert opt["skills"] == ["optimizacion"]
    assert opt["is_leaf"] is True


def test_describe_standalone_default(tmp_path):
    cfg = _Cfg(org_role="", org_topology_file=str(tmp_path / "missing.json"))
    desc = management.describe(cfg)
    assert desc["standalone"] is True
    assert desc["root"] == "general"


# ── available skills (what CAN be assigned) ─────────────────────────────────────────


def test_available_skills_lists_registry_domains():
    skills = management.available_skills()
    domains = {s["domain"] for s in skills}
    assert "optimizacion" in domains


def test_validate_skills_warns_but_keeps_unknown():
    cleaned, unknown = management.validate_skills(["optimizacion", "totally_new"])
    assert cleaned == ("optimizacion", "totally_new")
    assert unknown == ["totally_new"]
