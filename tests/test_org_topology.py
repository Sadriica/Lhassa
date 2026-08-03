"""Tests for lhassa.org.topology — the config-driven org CHART (Fase 7, slice E).

A topology is a TREE of brains: one standalone brain up to an arbitrary-depth
hierarchy. These tests cover the default derivation (standalone vs flat), the JSON
load/save round-trip, validation (cycles / multiple roots / missing parent /
duplicate ids), and the tree queries.
"""

import json

import pytest

from lhassa.org import topology as topo
from lhassa.org.topology import BrainNode, Topology, TopologyError


class _Cfg:
    """A tiny stand-in for Config with just the topology-relevant fields."""

    def __init__(self, *, org_role="", org_departments="", org_topology_file=""):
        self.org_role = org_role
        self.org_departments = org_departments
        self.org_topology_file = org_topology_file


# ── default derivation ──────────────────────────────────────────────────────────


def test_default_standalone_single_admin_brain():
    # The default/main brain IS Lhassa — the permanent administrator at the root.
    t = topo.default_topology(_Cfg(org_role=""))
    assert t.is_standalone()
    assert len(t.nodes()) == 1
    assert t.root().id == "lhassa"
    assert t.root().role == "administrator"
    assert t.is_admin("lhassa")
    assert t.children_of("lhassa") == []


def test_default_flat_admin_plus_departments():
    # org_role/org_departments imply departments hanging UNDER the administrator.
    t = topo.default_topology(
        _Cfg(org_role="general", org_departments="optimization,documentation,finance")
    )
    assert not t.is_standalone()
    assert t.root().id == "lhassa"
    assert t.root().role == "administrator"
    kids = [c.id for c in t.children_of("lhassa")]
    assert kids == ["dept:optimization", "dept:documentation", "dept:finance"]
    opt = t.node("dept:optimization")
    assert opt.role == "department"
    assert opt.department == "optimization"
    assert opt.parent == "lhassa"


def test_load_falls_back_to_default_when_no_file(tmp_path):
    cfg = _Cfg(org_role="", org_topology_file=str(tmp_path / "missing.json"))
    t = topo.load_topology(cfg)
    assert t.is_standalone()
    assert t.root().id == "lhassa"


# ── save / load round-trip ──────────────────────────────────────────────────────


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "org.json"
    cfg = _Cfg(org_topology_file=str(path))
    original = Topology(
        [
            BrainNode(id="general", role="general"),
            BrainNode(id="ops", role="sub_general", parent="general"),
            BrainNode(
                id="dept:optimization",
                role="department",
                department="optimization",
                parent="ops",
            ),
        ]
    )
    written = topo.save_topology(original, cfg)
    assert written == path
    assert path.exists()

    # The on-disk file is a JSON list of node objects.
    raw = json.loads(path.read_text())
    assert isinstance(raw, list)
    assert {n["id"] for n in raw} == {"general", "ops", "dept:optimization"}

    # On load, the permanent administrator (Lhassa) is injected at the root and the
    # loaded chart's old root ("general") becomes its child — the rest is intact.
    reloaded = topo.load_topology(cfg)
    assert reloaded.root().id == "lhassa"
    assert [n.id for n in reloaded.nodes()] == [
        "lhassa", "general", "ops", "dept:optimization",
    ]
    assert reloaded.node("general").parent == "lhassa"
    assert reloaded.node("dept:optimization").department == "optimization"
    assert reloaded.node("ops").parent == "general"


def test_load_accepts_object_with_nodes_key(tmp_path):
    path = tmp_path / "org.json"
    path.write_text(json.dumps({"nodes": [{"id": "solo", "role": "general"}]}))
    cfg = _Cfg(org_topology_file=str(path))
    t = topo.load_topology(cfg)
    # the administrator is synthesized above the loaded root
    assert t.root().id == "lhassa"
    assert t.has("solo")
    assert t.node("solo").parent == "lhassa"


# ── permanent administrator (Lhassa orbits everything) ──────────────────────────


def test_load_respects_a_chart_already_rooted_at_admin(tmp_path):
    path = tmp_path / "org.json"
    path.write_text(json.dumps([
        {"id": "lhassa", "role": "administrator", "parent": None},
        {"id": "dept:x", "role": "department", "department": "x", "parent": "lhassa"},
    ]))
    cfg = _Cfg(org_topology_file=str(path))
    t = topo.load_topology(cfg)
    assert t.root().id == "lhassa"
    assert [n.id for n in t.nodes()] == ["lhassa", "dept:x"]
    assert t.node("dept:x").parent == "lhassa"
    assert t.is_admin("lhassa") and not t.is_admin("dept:x")


# ── validation ──────────────────────────────────────────────────────────────────


def test_rejects_no_nodes():
    with pytest.raises(TopologyError):
        Topology([])


def test_rejects_duplicate_ids():
    with pytest.raises(TopologyError):
        Topology([BrainNode(id="a"), BrainNode(id="a")])


def test_rejects_multiple_roots():
    with pytest.raises(TopologyError):
        Topology([BrainNode(id="a"), BrainNode(id="b")])


def test_rejects_missing_parent():
    with pytest.raises(TopologyError):
        Topology(
            [BrainNode(id="a"), BrainNode(id="b", parent="ghost")]
        )


def test_rejects_cycle():
    # No node has parent None → no root → a cycle, rejected.
    with pytest.raises(TopologyError):
        Topology(
            [
                BrainNode(id="a", parent="b"),
                BrainNode(id="b", parent="a"),
            ]
        )


# ── tree queries ────────────────────────────────────────────────────────────────


def _three_level() -> Topology:
    return Topology(
        [
            BrainNode(id="general", role="general"),
            BrainNode(id="ops", role="sub_general", parent="general"),
            BrainNode(id="biz", role="sub_general", parent="general"),
            BrainNode(
                id="dept:optimization",
                role="department",
                department="optimization",
                parent="ops",
            ),
            BrainNode(
                id="dept:finance", role="department", department="finance", parent="biz"
            ),
        ]
    )


def test_children_of_and_descendants():
    t = _three_level()
    assert [c.id for c in t.children_of("general")] == ["ops", "biz"]
    assert [c.id for c in t.children_of("ops")] == ["dept:optimization"]
    desc = {n.id for n in t.descendants("general")}
    assert desc == {"ops", "biz", "dept:optimization", "dept:finance"}
    assert t.is_leaf("dept:optimization")
    assert not t.is_leaf("ops")


def test_arbitrary_depth_node_lookup():
    t = _three_level()
    assert t.root().id == "general"
    assert t.node("dept:finance").department == "finance"
    with pytest.raises(KeyError):
        t.node("nope")


# ── coordination: walking the tree (root → leaf, result bubbles up) ─────────────

import pytest_asyncio  # noqa: E402,F401

from lhassa.org import messaging  # noqa: E402
from lhassa.org.coordinator import (  # noqa: E402
    route_in_topology,
    route_scores_in_topology,
    run_goal,
)
from lhassa.org.messaging import MessageKind  # noqa: E402

INST = "inst_personal"


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


def test_route_in_topology_picks_the_matching_subtree():
    t = _three_level()
    # the goal is an optimization one → routes into ops (which owns optimization)
    assert route_in_topology(t, "general", "optimize the slow latency") == "ops"
    # a finance goal → routes into biz (which owns finance)
    assert route_in_topology(t, "general", "reconcile the budget invoice") == "biz"


@pytest.mark.asyncio
async def test_three_level_routes_to_leaf_and_bubbles_up(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    t = _three_level()

    ran = {}

    def executor(goal, *, department):
        ran["department"] = department
        ran["goal"] = goal
        return {"summary": f"done:{department}", "ok": True}

    out = await run_goal(
        "optimize the slow latency, it is too slow",
        by_actor="owner",
        topology=t,
        instance_id=INST,
        executor=executor,
    )

    # routed general → ops → dept:optimization (the correct leaf)
    assert out["path"] == ["general", "ops", "dept:optimization"]
    assert out["leaf"] == "dept:optimization"
    assert out["department"] == "optimization"
    assert out["result"] == {"summary": "done:optimization", "ok": True}

    # the LEAF executed (a coordinator never runs the executor)
    assert ran == {"department": "optimization", "goal": out["goal"]}

    # the result bubbled back to the root as a TASK_RESULT to "general"
    bubbled = await messaging.by_corr(
        out["corr_id"], instance_id=INST, kind=MessageKind.TASK_RESULT
    )
    to_general = [m for m in bubbled if m.to_instance == "general"]
    assert to_general, "the subtree result must bubble up to the root"


@pytest.mark.asyncio
async def test_coordinators_never_execute(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    t = _three_level()

    seen_departments = []

    def executor(goal, *, department):
        seen_departments.append(department)
        return {"summary": "x"}

    await run_goal(
        "reconcile the budget", by_actor="owner", topology=t,
        instance_id=INST, executor=executor,
    )
    # ONLY the leaf department executed — never "general"/"ops"/"biz".
    assert seen_departments == ["finance"]


@pytest.mark.asyncio
async def test_single_brain_executes_directly(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    standalone = Topology([BrainNode(id="general", role="general")])

    def executor(goal, *, department):
        return {"summary": f"solo:{department}", "ok": True}

    out = await run_goal(
        "do the thing", by_actor="owner", topology=standalone,
        instance_id=INST, executor=executor,
    )
    assert out["standalone"] is True
    assert out["path"] == ["general"]
    assert out["leaf"] == "general"
    assert out["result"] == {"summary": "solo:general", "ok": True}


@pytest.mark.asyncio
async def test_walk_writes_org_episode(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    t = _three_level()

    await run_goal(
        "optimize the slow loop", by_actor="owner", topology=t,
        instance_id=INST, executor=lambda goal, *, department: {"summary": "ok"},
    )
    eps = await db.load_recent_episodes(INST)
    assert any(e["despertar_por"] == "org" for e in eps)


@pytest.mark.asyncio
async def test_high_risk_self_approval_rejected_in_tree(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import SelfApprovalError

    t = _three_level()
    ran = {"n": 0}

    def executor(goal, *, department):
        ran["n"] += 1
        return {"summary": "ok"}

    # same actor as proposer cannot self-approve a high-risk leaf execution
    with pytest.raises(SelfApprovalError):
        await run_goal(
            "reconcile the budget", by_actor="owner", topology=t, instance_id=INST,
            executor=executor, high_risk=True, approver="owner",
        )
    assert ran["n"] == 0

    # a DISTINCT approver lets it proceed
    out = await run_goal(
        "reconcile the budget", by_actor="owner", topology=t, instance_id=INST,
        executor=executor, high_risk=True, approver="manager",
    )
    assert ran["n"] == 1
    assert out["leaf"] == "dept:finance"


# ── fan-out + synthesis (Etapa 3 — real delegation) ──────────────────────────────


def test_route_scores_exposes_the_whole_ranking():
    t = _three_level()
    scores = route_scores_in_topology(t, "general", "optimize the slow latency")
    ids = [child.id for child, _score in scores]
    assert ids[0] == "ops"  # the winner is first
    assert set(ids) == {"ops", "biz"}


@pytest.mark.asyncio
async def test_single_winner_stays_single_route_no_synthesis(tmp_path, monkeypatch):
    """A clear single winner is unaffected: same shape/cost as before fan-out."""
    await _init(tmp_path, monkeypatch)
    t = _three_level()

    out = await run_goal(
        "optimize the slow latency", by_actor="owner", topology=t,
        instance_id=INST, executor=lambda goal, *, department: {"summary": "ok"},
    )
    assert "multi" not in out
    assert out["leaf"] == "dept:optimization"
    assert isinstance(out["department"], str)


@pytest.mark.asyncio
async def test_tied_departments_fan_out_and_synthesize_offline(tmp_path, monkeypatch):
    """A goal that ties two departments fans out to both and merges offline, $0."""
    await _init(tmp_path, monkeypatch)
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)
    t = _three_level()

    seen = []

    def executor(goal, *, department):
        seen.append(department)
        return {"summary": f"handled by {department}"}

    # "optimize" ties ops (optimization) and "budget" ties biz (finance) — 1-1 tie.
    out = await run_goal(
        "optimize the budget", by_actor="owner", topology=t,
        instance_id=INST, executor=executor,
    )

    assert out["multi"] is True
    assert set(seen) == {"optimization", "finance"}
    assert set(out["department"]) == {"optimization", "finance"}
    assert len(out["branches"]) == 2
    assert "Synthesis for:" in out["result"]
    assert "optimization" in out["result"] and "finance" in out["result"]

    db = await _init(tmp_path, monkeypatch)
    eps = await db.load_recent_episodes(INST)
    assert any("synthesis" in (e.get("decision") or "") for e in eps)


@pytest.mark.asyncio
async def test_explicit_multi_forces_fan_out_without_a_tie(tmp_path, monkeypatch):
    """params={"multi": True} fans out even with a clear single winner."""
    await _init(tmp_path, monkeypatch)
    t = _three_level()

    def executor(goal, *, department):
        return {"summary": f"handled by {department}"}

    out = await run_goal(
        "optimize the slow latency", by_actor="owner", topology=t,
        instance_id=INST, executor=executor, params={"multi": True},
    )
    assert out["multi"] is True
    assert len(out["branches"]) == 2  # both ops and biz — capped at 2 children here


@pytest.mark.asyncio
async def test_fan_out_live_synthesis_calls_brain_executor(tmp_path, monkeypatch):
    """Under LHASSA_ORG_LIVE, synthesis is ONE extra think via brain_executor."""
    await _init(tmp_path, monkeypatch)
    monkeypatch.setenv("LHASSA_ORG_LIVE", "1")
    t = _three_level()

    calls = []

    async def fake_brain_executor(prompt, *, department, instance_id=None, config=None):
        calls.append((prompt, department))
        return {"summary": "merged live answer"}

    import lhassa.org.coordinator as coordinator_mod
    monkeypatch.setattr(coordinator_mod, "brain_executor", fake_brain_executor)

    out = await run_goal(
        "optimize the budget", by_actor="owner", topology=t,
        instance_id=INST, executor=lambda goal, *, department: {"summary": department},
    )
    assert out["multi"] is True
    assert out["result"] == "merged live answer"
    assert len(calls) == 1
    assert calls[0][1] == coordinator_mod.GENERAL_IDENTITY
