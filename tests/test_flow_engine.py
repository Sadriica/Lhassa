"""Work-flow ENGINE tests — offline, with an injected executor (no live model)."""

import asyncio
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
async def test_opt_in_full_context_preserves_initial_dossier_and_every_stage(offline_env):
    calls = []

    async def executor(goal, *, department, config, **kwargs):
        calls.append((department, goal))
        return {"summary": f"{department}-output", "model": "stub", "cost_usd": 0}

    flow = Flow(
        id="research-context",
        name="research context",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode(
                "collector",
                NodeKind.TASK,
                {
                    "brain": "collector",
                    "prompt": "COLLECT {input}",
                    "input_mode": "full",
                    "preserve_context": True,
                },
            ),
            FlowNode(
                "analyst",
                NodeKind.TASK,
                {
                    "brain": "analyst",
                    "prompt": "ANALYZE {input}",
                    "input_mode": "full",
                    "preserve_context": True,
                },
            ),
        ],
        edges=[FlowEdge("t", "collector"), FlowEdge("collector", "analyst")],
    )
    record = await run_flow(
        flow,
        inputs={
            "summary": "the idea",
            "evidence_bundle": {"sources": [{"id": "S1", "sha256": "abc"}]},
        },
        config=offline_env,
        executor=executor,
    )
    assert record["status"] == "completed"
    assert '"sha256": "abc"' in calls[0][1]
    assert '"sha256": "abc"' in calls[1][1]
    assert "collector-output" in calls[1][1]
    assert record["output"]["research_context"]["stages"][-1]["brain"] == "analyst"


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


@pytest.mark.asyncio
async def test_durable_approval_pauses_retries_and_resumes_from_cursor(
    offline_env, monkeypatch
):
    from lhassa.security import approval

    async def unexpected_parking(*args, **kwargs):
        raise AssertionError("durable parking belongs to the checkpoint hook")

    monkeypatch.setattr(approval, "request_approval", unexpected_parking)
    flow = Flow(
        id="durable",
        name="durable approval",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"skill": "optimization", "prompt": "do {input}"}),
            FlowNode("g", NodeKind.GATE, {"type": "approval"}),
            FlowNode("o", NodeKind.OUTPUT, {"type": "log", "target": "audit"}),
        ],
        edges=[FlowEdge("t", "w"), FlowEdge("w", "g"), FlowEdge("g", "o")],
    )
    calls = []
    checkpoints = []

    async def save(cursor):
        # A checkpoint must be detached and persistable without a custom encoder.
        checkpoints.append(json.loads(json.dumps(cursor)))

    first = await run_flow(
        flow,
        inputs={"summary": "seed"},
        config=offline_env,
        executor=_recording_executor(calls),
        run_id="run_fixed",
        checkpoint_hook=save,
        approval_context={
            "requested_by": "owner",
            "run_id": "untrusted",
            "flow_id": "untrusted",
            "node_id": "untrusted",
        },
    )

    assert first["status"] == "waiting_approval"
    assert first["finished_at"] is None
    assert [step["node"] for step in first["steps"]] == ["t", "w", "g"]
    assert first["steps"][-1]["status"] == "needs_approval"
    cursor = first["resume_state"]
    gate_data = first["steps"][1]["result"]
    assert cursor["frontier"] == [{"node": "g", "data": gate_data}]
    assert "g" not in cursor["visited"]
    assert cursor["pending_approval"]["node_id"] == "g"
    assert json.loads(json.dumps(cursor)) == cursor

    approval_id = first["steps"][-1]["approval_id"]
    approval_payload = {
        "accion": "flow_gate",
        "node": "g",
        "node_id": "g",
        "data": gate_data,
        "run_id": "run_fixed",
        "flow_id": "durable",
        "requested_by": "owner",
    }
    assert first["steps"][-1]["approval_payload"] == approval_payload
    assert cursor["pending_approval"] == {
        "node_id": "g",
        "approval_id": approval_id,
        "approval_payload": approval_payload,
        "step_index": 2,
    }

    # Reclaiming before a decision replays the gate, but neither the deterministic
    # parking command nor the gate step changes or duplicates.
    retried = await run_flow(
        flow,
        config=offline_env,
        executor=_recording_executor(calls),
        run_id="run_fixed",
        resume_state=cursor,
        checkpoint_hook=save,
        approval_context={"requested_by": "owner"},
    )
    assert retried["status"] == "waiting_approval"
    assert len([step for step in retried["steps"] if step["node"] == "g"]) == 1
    assert retried["steps"][-1]["approval_id"] == approval_id
    assert retried["resume_state"]["pending_approval"]["approval_payload"] == approval_payload

    resumed = await run_flow(
        flow,
        config=offline_env,
        executor=_recording_executor(calls),
        run_id="run_fixed",
        resume_state=retried["resume_state"],
        checkpoint_hook=save,
        approval_decisions={"g": True},
    )
    assert resumed["status"] == "completed"
    assert resumed["finished_at"] is not None
    assert [step["node"] for step in resumed["steps"]] == ["t", "w", "g", "o"]
    gate = resumed["steps"][2]
    assert gate["passed"] is True
    assert gate["needs_approval"] is False
    assert gate["approval_id"] == approval_id
    assert gate["approval_payload"] == approval_payload
    assert len(calls) == 1
    assert resumed["resume_state"]["frontier"] == []
    assert resumed["resume_state"]["status"] == "completed"


def _retry_flow(retry_cfg: dict, *, timeout_seconds=None) -> Flow:
    cfg = {"skill": "optimization", "prompt": "go", "retry": retry_cfg}
    if timeout_seconds is not None:
        cfg["timeout_seconds"] = timeout_seconds
    return Flow(
        id="retry",
        name="retry",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, cfg),
            FlowNode("o", NodeKind.OUTPUT, {"type": "log"}),
        ],
        edges=[FlowEdge("t", "w"), FlowEdge("w", "o")],
    )


@pytest.mark.asyncio
async def test_task_retry_inline_succeeds_after_transient_failure(offline_env):
    """A non-durable (in-memory) run retries inline, no real sleep, no extra step."""
    calls = []

    async def flaky(goal, *, department, config, **kw):
        calls.append(goal)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return {"ok": True, "summary": "done"}

    flow = _retry_flow({"max_attempts": 2, "retry_on": ["error"]})
    rec = await run_flow(flow, config=offline_env, executor=flaky)
    assert rec["status"] == "completed"
    assert len(calls) == 2
    task_step = next(s for s in rec["steps"] if s["kind"] == "task")
    assert task_step["ok"] is True
    assert task_step["attempt"] == 2
    # exactly one step recorded for the task node (no per-attempt duplicate)
    assert len([s for s in rec["steps"] if s["kind"] == "task"]) == 1


@pytest.mark.asyncio
async def test_task_retry_exhausted_fails_the_path(offline_env):
    async def always_boom(goal, *, department, config, **kw):
        raise RuntimeError("permanent")

    flow = _retry_flow({"max_attempts": 2, "retry_on": ["error"]})
    rec = await run_flow(flow, config=offline_env, executor=always_boom)
    assert rec["status"] == "failed"
    task_step = next(s for s in rec["steps"] if s["kind"] == "task")
    assert task_step["ok"] is False and "permanent" in task_step["error"]
    assert not any(s["kind"] == "output" for s in rec["steps"])


@pytest.mark.asyncio
async def test_task_default_config_retries_once_unchanged(offline_env):
    """No ``retry`` config at all behaves exactly as before: a single attempt."""
    calls = []

    async def boom(goal, *, department, config, **kw):
        calls.append(goal)
        raise RuntimeError("no retry configured")

    flow = _retry_flow({})
    rec = await run_flow(flow, config=offline_env, executor=boom)
    assert rec["status"] == "failed"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_task_timeout_counts_as_retry_on_timeout(offline_env):
    calls = []

    async def slow_then_fast(goal, *, department, config, **kw):
        calls.append(goal)
        if len(calls) == 1:
            await asyncio.sleep(0.2)
            return {"ok": True, "summary": "too slow"}
        return {"ok": True, "summary": "fast"}

    flow = _retry_flow(
        {"max_attempts": 2, "retry_on": ["timeout"]}, timeout_seconds=0.05
    )
    rec = await run_flow(flow, config=offline_env, executor=slow_then_fast)
    assert rec["status"] == "completed"
    assert len(calls) == 2
    task_step = next(s for s in rec["steps"] if s["kind"] == "task")
    assert task_step["attempt"] == 2


@pytest.mark.asyncio
async def test_durable_retry_parks_and_resumes_from_cursor(offline_env):
    """A durable run parks WAITING_RETRY, releasing the walk, and resumes clean."""
    calls = []

    async def flaky(goal, *, department, config, **kw):
        calls.append(goal)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return {"ok": True, "summary": "done"}

    flow = _retry_flow({"max_attempts": 2, "retry_on": ["error"], "base_seconds": 0})
    checkpoints = []

    async def save(cursor):
        checkpoints.append(json.loads(json.dumps(cursor)))

    first = await run_flow(
        flow,
        config=offline_env,
        executor=flaky,
        run_id="run_retry",
        checkpoint_hook=save,
    )
    assert first["status"] == "waiting_retry"
    assert first["finished_at"] is None
    assert [s["node"] for s in first["steps"]] == ["t", "w"]
    assert first["steps"][-1]["status"] == "waiting_retry"
    cursor = first["resume_state"]
    assert cursor["frontier"] == [{"node": "w", "data": {}}]
    assert "w" not in cursor["visited"]
    assert cursor["retry_state"] == {"w": 1}
    assert cursor["pending_retry"]["node_id"] == "w"
    assert json.loads(json.dumps(cursor)) == cursor

    resumed = await run_flow(
        flow,
        config=offline_env,
        executor=flaky,
        run_id="run_retry",
        resume_state=cursor,
        checkpoint_hook=save,
    )
    assert resumed["status"] == "completed"
    assert resumed["finished_at"] is not None
    assert [s["node"] for s in resumed["steps"]] == ["t", "w", "o"]
    task_step = resumed["steps"][1]
    assert task_step["ok"] is True and task_step["attempt"] == 2
    assert len(calls) == 2
    assert resumed["resume_state"]["retry_state"] == {}
    assert resumed["resume_state"]["status"] == "completed"


def _loop_flow(*, max_iterations=3, verify=None) -> Flow:
    loop_cfg = {"max_iterations": max_iterations, "body": "b1"}
    if verify is not None:
        loop_cfg["verify"] = verify
    return Flow(
        id="loopy",
        name="loopy",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("loop", NodeKind.LOOP, loop_cfg),
            FlowNode("b1", NodeKind.TASK, {"brain": "x", "prompt": "iterate {input}"}),
            FlowNode("done", NodeKind.OUTPUT, {"type": "log"}),
        ],
        edges=[
            FlowEdge("t", "loop"),
            FlowEdge("loop", "b1"),
            FlowEdge("b1", "loop"),
            FlowEdge("loop", "done"),
        ],
    )


@pytest.mark.asyncio
async def test_loop_runs_until_max_iterations_with_no_verifier(offline_env):
    calls = []

    async def counter(goal, *, department, config, **kw):
        calls.append(goal)
        return {"ok": True, "summary": f"n={len(calls)}"}

    flow = _loop_flow(max_iterations=3)
    rec = await run_flow(flow, config=offline_env, executor=counter)
    assert rec["status"] == "completed"
    assert len(calls) == 3
    loop_step = next(s for s in rec["steps"] if s["kind"] == "loop")
    assert loop_step["loop"] == {
        "iterations": 3, "max_iterations": 3, "stop_reason": "max_iterations",
    }
    assert len(loop_step["iterations"]) == 3
    assert any(s["kind"] == "output" for s in rec["steps"])


@pytest.mark.asyncio
async def test_loop_stops_early_when_verifier_passes(offline_env):
    calls = []

    async def counter(goal, *, department, config, **kw):
        calls.append(goal)
        # only "done" on the 2nd attempt
        return {"ok": True, "summary": "done" if len(calls) == 2 else "not yet"}

    flow = _loop_flow(
        max_iterations=5, verify={"type": "regex", "pattern": "done"}
    )
    rec = await run_flow(flow, config=offline_env, executor=counter)
    assert rec["status"] == "completed"
    assert len(calls) == 2  # bounded — stopped as soon as the verifier passed
    loop_step = next(s for s in rec["steps"] if s["kind"] == "loop")
    assert loop_step["loop"]["stop_reason"] == "verified"
    assert loop_step["loop"]["iterations"] == 2


@pytest.mark.asyncio
async def test_loop_body_approval_gate_is_refused(offline_env):
    """A loop body is bounded & verifier-terminated — a human-in-the-loop gate
    (approval) would break that contract, so it is refused, not silently run."""
    flow = Flow(
        id="bad-loop", name="bad-loop",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("loop", NodeKind.LOOP, {"max_iterations": 2, "body": "g"}),
            FlowNode("g", NodeKind.GATE, {"type": "approval"}),
        ],
        edges=[FlowEdge("t", "loop"), FlowEdge("g", "loop")],
    )
    rec = await run_flow(flow, config=offline_env)
    assert rec["status"] == "failed"
    loop_step = next(s for s in rec["steps"] if s["kind"] == "loop")
    assert loop_step["ok"] is False and "approval" in loop_step["error"]


@pytest.mark.asyncio
async def test_wait_node_parks_and_resumes_like_an_approval(offline_env):
    flow = Flow(
        id="waiting", name="waiting",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.WAIT, {}),
            FlowNode("o", NodeKind.OUTPUT, {"type": "log"}),
        ],
        edges=[FlowEdge("t", "w"), FlowEdge("w", "o")],
    )
    checkpoints = []

    async def save(cursor):
        checkpoints.append(json.loads(json.dumps(cursor)))

    first = await run_flow(
        flow, config=offline_env, run_id="run_wait", checkpoint_hook=save
    )
    assert first["status"] == "waiting_approval"
    assert first["steps"][-1]["status"] == "needs_approval"

    resumed = await run_flow(
        flow,
        config=offline_env,
        run_id="run_wait",
        resume_state=first["resume_state"],
        checkpoint_hook=save,
        approval_decisions={"w": True},
    )
    assert resumed["status"] == "completed"
    assert [s["node"] for s in resumed["steps"]] == ["t", "w", "o"]


@pytest.mark.asyncio
async def test_subflow_composes_a_child_flow_inline(offline_env):
    child = Flow(
        id="child",
        name="child",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"brain": "x", "prompt": "child work"}),
        ],
        edges=[FlowEdge("t", "w")],
    )

    async def loader(flow_id):
        return child if flow_id == "child" else None

    parent = Flow(
        id="parent",
        name="parent",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("s", NodeKind.SUBFLOW, {"flow_id": "child"}),
        ],
        edges=[FlowEdge("t", "s")],
    )
    calls = []

    async def executor(goal, *, department, config, **kw):
        calls.append(goal)
        return {"ok": True, "summary": "child done"}

    rec = await run_flow(
        parent, config=offline_env, executor=executor, subflow_loader=loader
    )
    assert rec["status"] == "completed"
    assert len(calls) == 1
    subflow_step = next(s for s in rec["steps"] if s["kind"] == "subflow")
    assert subflow_step["subflow"] == "child"
    assert subflow_step["subflow_status"] == "completed"


@pytest.mark.asyncio
async def test_subflow_missing_flow_id_fails_the_path(offline_env):
    async def loader(flow_id):
        return None

    flow = Flow(
        id="parent2", name="parent2",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("s", NodeKind.SUBFLOW, {"flow_id": "missing"}),
        ],
        edges=[FlowEdge("t", "s")],
    )
    rec = await run_flow(flow, config=offline_env, subflow_loader=loader)
    assert rec["status"] == "failed"
    step = next(s for s in rec["steps"] if s["kind"] == "subflow")
    assert step["ok"] is False and "not found" in step["error"]


@pytest.mark.asyncio
async def test_subflow_recursion_depth_is_bounded(offline_env):
    """A subflow that (transitively) composes itself is refused, not a stack
    overflow — the bounded recursion-depth guard."""
    recursive = Flow(
        id="recursive",
        name="recursive",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("s", NodeKind.SUBFLOW, {"flow_id": "recursive"}),
        ],
        edges=[FlowEdge("t", "s")],
    )

    async def loader(flow_id):
        return recursive if flow_id == "recursive" else None

    rec = await run_flow(recursive, config=offline_env, subflow_loader=loader)
    assert rec["status"] == "failed"
    step = next(s for s in rec["steps"] if s["kind"] == "subflow")
    assert step["ok"] is False and "recursion" in step["error"]


@pytest.mark.asyncio
async def test_durable_parking_is_deferred_while_legacy_remains_best_effort(
    offline_env, monkeypatch
):
    from lhassa.security import approval

    calls = []

    async def cannot_park(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(approval, "request_approval", cannot_park)
    flow = Flow(
        id="approval-failure",
        name="approval failure",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {}),
            FlowNode("g", NodeKind.GATE, {"type": "approval"}),
        ],
        edges=[FlowEdge("t", "g")],
    )

    legacy = await run_flow(flow, config=offline_env)
    assert legacy["status"] == "completed"
    assert legacy["steps"][-1]["status"] == "needs_approval"
    assert legacy["steps"][-1]["approval_id"] is None
    assert len(calls) == 1

    async def checkpoint(_cursor):
        return None

    durable = await run_flow(
        flow,
        config=offline_env,
        run_id="run_cannot_park",
        checkpoint_hook=checkpoint,
    )
    assert durable["status"] == "waiting_approval"
    assert durable["steps"][-1]["status"] == "needs_approval"
    assert durable["steps"][-1]["approval_id"].startswith("ap_flow_")
    assert durable["resume_state"]["pending_approval"]["approval_payload"][
        "run_id"
    ] == "run_cannot_park"
    assert len(calls) == 1
