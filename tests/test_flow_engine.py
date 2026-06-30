"""Work-flow ENGINE tests — offline, with an injected executor (no live model)."""

import json

import pytest

from lhassa.flows.engine import eval_when, resolve_brain, run_flow
from lhassa.flows.model import Flow, FlowEdge, FlowNode, NodeKind


@pytest.fixture
def offline_env(tmp_path, monkeypatch):
    """A temp topology for routing + LHASSA_ORG_LIVE forced off."""
    import lhassa.config as config_mod

    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)
    topo = tmp_path / "org.json"
    topo.write_text(json.dumps([
        {"id": "general", "role": "general", "department": None, "parent": None},
        {"id": "dept:optimization", "role": "department",
         "department": "optimization", "parent": "general"},
        {"id": "dept:finance", "role": "department",
         "department": "finance", "parent": "general"},
    ]))
    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(topo))
    return config_mod.Config()


def _recording_executor(calls):
    async def _exec(goal, *, department, config, **kw):
        calls.append({"goal": goal, "department": department})
        return {"ok": True, "summary": f"done:{goal}", "department": department}
    return _exec


# ── eval_when ──────────────────────────────────────────────────────────────────
def test_eval_when_basics():
    assert eval_when(None, {}) is True
    assert eval_when({"field": "passed", "op": "truthy"}, {"passed": True}) is True
    assert eval_when({"field": "passed", "op": "falsy"}, {"passed": True}) is False
    assert eval_when({"field": "status", "op": "eq", "value": "ok"}, {"status": "ok"}) is True
    assert eval_when({"op": "bogus"}, {}) is False  # unknown op → not taken, no raise


# ── hybrid routing ─────────────────────────────────────────────────────────────
def test_resolve_brain_pinned(offline_env):
    node = FlowNode("w", NodeKind.TASK, {"brain": "dept:finance", "skill": "optimization"})
    dept, pinned = resolve_brain(node, None, offline_env)
    assert (dept, pinned) == ("dept:finance", True)


def test_resolve_brain_auto_routes_by_skill(offline_env):
    node = FlowNode("w", NodeKind.TASK, {"skill": "optimization latency"})
    dept, pinned = resolve_brain(node, None, offline_env)
    assert pinned is False
    assert dept == "optimization"  # routed to the optimization leaf


# ── a full run: trigger → task → gate → output, threading data ─────────────────
def _linear_flow():
    return Flow(
        id="f",
        name="line",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"skill": "optimization", "prompt": "improve {input}"}),
            FlowNode("g", NodeKind.GATE, {"type": "verify", "spec": {"type": "regex", "pattern": "done"}}),
            FlowNode("o", NodeKind.OUTPUT, {"type": "log", "target": "audit"}),
        ],
        edges=[FlowEdge("t", "w"), FlowEdge("w", "g"), FlowEdge("g", "o")],
    )


@pytest.mark.asyncio
async def test_run_linear_flow_threads_data_and_records_gate(offline_env):
    calls = []
    rec = await run_flow(
        _linear_flow(), inputs={"summary": "slow query"},
        config=offline_env, executor=_recording_executor(calls),
    )
    assert rec["status"] == "completed"
    kinds = [s["kind"] for s in rec["steps"]]
    assert kinds == ["trigger", "task", "gate", "output"]

    # data flows along the edge: the trigger input is interpolated into the task goal
    assert calls[0]["goal"] == "improve slow query"
    assert calls[0]["department"] == "optimization"

    # the task output threads into the verify gate, which passes ("done:..." matches "done")
    gate_step = next(s for s in rec["steps"] if s["kind"] == "gate")
    assert gate_step["gate"] == "verify" and gate_step["passed"] is True

    # the output node records the result
    out_step = next(s for s in rec["steps"] if s["kind"] == "output")
    assert out_step["output_type"] == "log" and out_step["target"] == "audit"


@pytest.mark.asyncio
async def test_failing_gate_stops_the_path(offline_env):
    flow = _linear_flow()
    flow.node("g").config["spec"] = {"type": "regex", "pattern": "NEVER_MATCHES"}
    calls = []
    rec = await run_flow(flow, config=offline_env, executor=_recording_executor(calls))
    assert rec["status"] == "completed"  # a non-pass is recorded data, not a crash
    gate_step = next(s for s in rec["steps"] if s["kind"] == "gate")
    assert gate_step["passed"] is False
    # the output after the gate is never reached
    assert not any(s["kind"] == "output" for s in rec["steps"])


@pytest.mark.asyncio
async def test_branch_takes_one_edge(offline_env):
    flow = Flow(
        id="b",
        name="branch",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("br", NodeKind.BRANCH, {}),
            FlowNode("yes", NodeKind.OUTPUT, {"type": "log", "target": "yes"}),
            FlowNode("no", NodeKind.OUTPUT, {"type": "log", "target": "no"}),
        ],
        edges=[
            FlowEdge("t", "br"),
            FlowEdge("br", "yes", when={"field": "go", "op": "eq", "value": True}),
            FlowEdge("br", "no"),  # unconditional = else
        ],
    )
    rec = await run_flow(flow, inputs={"go": True}, config=offline_env)
    targets = [s.get("target") for s in rec["steps"] if s["kind"] == "output"]
    assert targets == ["yes"]  # only the matching branch ran
    branch_step = next(s for s in rec["steps"] if s["kind"] == "branch")
    assert branch_step["chosen_edge"] == "yes"


@pytest.mark.asyncio
async def test_pinned_brain_overrides_routing(offline_env):
    flow = Flow(
        id="p", name="pin",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"brain": "dept:finance", "prompt": "report"}),
        ],
        edges=[FlowEdge("t", "w")],
    )
    calls = []
    rec = await run_flow(flow, config=offline_env, executor=_recording_executor(calls))
    assert calls[0]["department"] == "dept:finance"
    task_step = next(s for s in rec["steps"] if s["kind"] == "task")
    assert task_step["pinned"] is True


@pytest.mark.asyncio
async def test_node_error_becomes_failed_step(offline_env):
    async def boom(goal, *, department, config, **kw):
        raise RuntimeError("provider exploded")

    flow = Flow(
        id="e", name="err",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"skill": "optimization"}),
            FlowNode("o", NodeKind.OUTPUT, {"type": "log"}),
        ],
        edges=[FlowEdge("t", "w"), FlowEdge("w", "o")],
    )
    rec = await run_flow(flow, config=offline_env, executor=boom)
    assert rec["status"] == "failed"
    task_step = next(s for s in rec["steps"] if s["kind"] == "task")
    assert task_step["ok"] is False and "provider exploded" in task_step["error"]
    # the path stopped at the error — the output is not reached
    assert not any(s["kind"] == "output" for s in rec["steps"])


@pytest.mark.asyncio
async def test_invalid_flow_is_a_failed_run_not_a_raise(offline_env):
    flow = Flow(id="x", name="bad", nodes=[], edges=[])  # no trigger
    rec = await run_flow(flow, config=offline_env)
    assert rec["status"] == "failed"
    assert rec["steps"][0]["ok"] is False


@pytest.mark.asyncio
async def test_default_executor_offline_stub(offline_env):
    """With no injected executor and LHASSA_ORG_LIVE off, the org stub runs ($0)."""
    flow = Flow(
        id="d", name="default",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"skill": "optimization", "prompt": "go"}),
        ],
        edges=[FlowEdge("t", "w")],
    )
    rec = await run_flow(flow, config=offline_env)
    task_step = next(s for s in rec["steps"] if s["kind"] == "task")
    assert task_step["ok"] is True
    assert "[offline]" in task_step["result"]["summary"]
