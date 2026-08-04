"""Work-flow MODEL + STORE tests (offline, temp flows_file)."""

import json

import pytest

from lhassa.flows.model import (
    Flow,
    FlowEdge,
    FlowNode,
    FlowValidationError,
    NodeKind,
)
from lhassa.flows.store import FlowStore


# ── a small reusable sample flow: trigger → task → gate → output ──────────────
def sample_flow(flow_id: str = "flow1") -> Flow:
    return Flow(
        id=flow_id,
        name="Latency report",
        description="optimize then verify then notify",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("work", NodeKind.TASK, {"skill": "optimization", "prompt": "fix {input}"}),
            FlowNode("g", NodeKind.GATE, {"type": "verify", "spec": {"type": "regex", "pattern": "ok"}}),
            FlowNode("out", NodeKind.OUTPUT, {"type": "notify", "target": "owner"}),
        ],
        edges=[
            FlowEdge("t", "work"),
            FlowEdge("work", "g"),
            FlowEdge("g", "out"),
        ],
    )


# ── model ─────────────────────────────────────────────────────────────────────
def test_node_kind_is_closed():
    assert NodeKind.coerce("task") == NodeKind.TASK
    assert NodeKind.coerce(NodeKind.GATE) == NodeKind.GATE
    with pytest.raises(FlowValidationError):
        NodeKind.coerce("frobnicate")


def test_valid_flow_validates():
    flow = sample_flow().validate()
    assert flow.trigger().id == "t"


def test_roundtrip_preserves_everything():
    flow = sample_flow()
    rebuilt = Flow.from_dict(flow.to_dict())
    assert rebuilt.to_dict() == flow.to_dict()
    assert rebuilt.node("work").kind == NodeKind.TASK
    assert rebuilt.node("work").config["skill"] == "optimization"


def test_edge_accepts_loose_from_to_and_when():
    e = FlowEdge.from_dict({"from": "a", "to": "b", "when": {"field": "passed", "op": "truthy"}})
    assert e.from_node == "a" and e.to_node == "b"
    assert e.when == {"field": "passed", "op": "truthy"}


def test_duplicate_node_ids_rejected():
    flow = sample_flow()
    flow.nodes.append(FlowNode("work", NodeKind.OUTPUT, {}))
    with pytest.raises(FlowValidationError, match="duplicate"):
        flow.validate()


def test_exactly_one_trigger_required():
    flow = sample_flow()
    flow.nodes.append(FlowNode("t2", NodeKind.TRIGGER, {}))
    with pytest.raises(FlowValidationError, match="exactly one trigger"):
        flow.validate()
    # zero triggers too
    flow2 = sample_flow()
    flow2.nodes[0] = FlowNode("t", NodeKind.TASK, {"brain": "x"})
    with pytest.raises(FlowValidationError, match="exactly one trigger"):
        flow2.validate()


def test_edge_to_unknown_node_rejected():
    flow = sample_flow()
    flow.edges.append(FlowEdge("out", "ghost"))
    with pytest.raises(FlowValidationError, match="unknown node"):
        flow.validate()


def test_cycle_rejected():
    flow = sample_flow()
    flow.edges.append(FlowEdge("out", "work"))  # out → work closes a loop
    with pytest.raises(FlowValidationError, match="cycle"):
        flow.validate()


def test_no_edge_back_into_trigger():
    flow = sample_flow()
    flow.edges.append(FlowEdge("out", "t"))
    with pytest.raises(FlowValidationError, match="trigger"):
        flow.validate()


# ── loop / wait / subflow node kinds (Stream 2.3) ──────────────────────────────
def _loop_flow(*, max_iterations=3, body="b1", verify=None) -> Flow:
    loop_cfg = {"max_iterations": max_iterations, "body": body}
    if verify is not None:
        loop_cfg["verify"] = verify
    return Flow(
        id="loopy",
        name="loopy",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("loop", NodeKind.LOOP, loop_cfg),
            FlowNode("b1", NodeKind.TASK, {"brain": "x", "prompt": "iterate"}),
            FlowNode("done", NodeKind.OUTPUT, {"type": "log"}),
        ],
        edges=[
            FlowEdge("t", "loop"),
            FlowEdge("loop", "b1"),
            FlowEdge("b1", "loop"),  # the declared body back-edge
            FlowEdge("loop", "done"),
        ],
    )


def test_node_kind_is_closed_includes_new_kinds():
    assert NodeKind.coerce("loop") == NodeKind.LOOP
    assert NodeKind.coerce("wait") == NodeKind.WAIT
    assert NodeKind.coerce("subflow") == NodeKind.SUBFLOW


def test_loop_flow_validates_with_its_own_back_edge():
    flow = _loop_flow().validate()
    assert flow.node("loop").kind == NodeKind.LOOP


def test_loop_missing_max_iterations_rejected():
    flow = _loop_flow(max_iterations=None)
    with pytest.raises(FlowValidationError, match="max_iterations"):
        flow.validate()


def test_loop_zero_max_iterations_rejected():
    flow = _loop_flow(max_iterations=0)
    with pytest.raises(FlowValidationError, match="max_iterations"):
        flow.validate()


def test_loop_missing_body_rejected():
    flow = _loop_flow(body="")
    with pytest.raises(FlowValidationError, match="config.body"):
        flow.validate()


def test_loop_body_pointing_at_unknown_node_rejected():
    flow = _loop_flow(body="ghost")
    with pytest.raises(FlowValidationError, match="config.body"):
        flow.validate()


def test_a_cycle_outside_a_declared_loop_body_is_still_rejected():
    """The exception is narrow: only a loop's OWN declared back-edge is legal."""
    flow = sample_flow()
    flow.edges.append(FlowEdge("out", "work"))  # a plain cycle, no loop node involved
    with pytest.raises(FlowValidationError, match="cycle"):
        flow.validate()


def test_flow_with_zero_loop_nodes_validates_byte_for_byte_as_before():
    """Zero LOOP nodes anywhere: the acyclic check is untouched."""
    flow = sample_flow()
    before = flow.to_dict()
    flow.validate()
    assert flow.to_dict() == before  # validate() never mutates


def test_loop_roundtrips_through_json():
    flow = _loop_flow(verify={"type": "regex", "pattern": "done"})
    rebuilt = Flow.from_dict(flow.to_dict())
    rebuilt.validate()
    assert rebuilt.node("loop").config["max_iterations"] == 3
    assert rebuilt.node("loop").config["verify"] == {"type": "regex", "pattern": "done"}


def test_wait_and_subflow_nodes_validate_like_any_other_node():
    flow = Flow(
        id="composed",
        name="composed",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.WAIT, {}),
            FlowNode("s", NodeKind.SUBFLOW, {"flow_id": "child"}),
        ],
        edges=[FlowEdge("t", "w"), FlowEdge("w", "s")],
    )
    flow.validate()


# ── store ──────────────────────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    flows_file = tmp_path / "flows.json"
    monkeypatch.setattr(config_mod.Config, "flows_file", str(flows_file))
    return FlowStore()


def test_store_crud(store):
    assert store.list_flows() == []
    assert store.get("nope") is None

    flow = sample_flow("a")
    store.create(flow)
    assert {f.id for f in store.list_flows()} == {"a"}
    got = store.get("a")
    assert got is not None and got.name == "Latency report"

    # create twice → conflict
    with pytest.raises(FlowValidationError, match="already exists"):
        store.create(sample_flow("a"))

    # save overwrites
    edited = sample_flow("a")
    edited.name = "Renamed"
    store.save(edited)
    assert store.get("a").name == "Renamed"

    assert store.delete("a") is True
    assert store.delete("a") is False
    assert store.list_flows() == []


def test_store_write_is_atomic_and_valid_json(store):
    store.create(sample_flow("a"))
    store.create(sample_flow("b"))
    raw = json.loads(store.path.read_text())
    assert set(raw.keys()) == {"a", "b"}


def test_store_tolerates_corrupt_file(store):
    store.path.write_text("{not json")
    assert store.list_flows() == []  # degrades, no crash


def test_store_rejects_invalid_flow(store):
    bad = Flow(id="", name="x", nodes=[], edges=[])
    with pytest.raises(FlowValidationError):
        store.save(bad)
