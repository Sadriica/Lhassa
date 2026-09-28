"""Recovery behavior across durable flow orchestration boundaries."""

import json

import pytest

from lhassa.flows.durable import resume_flow_run, start_flow_run
from lhassa.flows.model import Flow, FlowEdge, FlowNode, NodeKind
from lhassa.flows.runtime import FlowRuntimeRepository, RunStatus


def _approval_flow() -> Flow:
    return Flow(
        id="durable-flow",
        name="durable",
        nodes=[
            FlowNode("trigger", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("work", NodeKind.TASK, {"brain": "general", "prompt": "draft"}),
            FlowNode("gate", NodeKind.GATE, {"type": "approval"}),
            FlowNode("output", NodeKind.OUTPUT, {"type": "log"}),
        ],
        edges=[
            FlowEdge("trigger", "work"),
            FlowEdge("work", "gate"),
            FlowEdge("gate", "output"),
        ],
    )


@pytest.mark.asyncio
async def test_idempotent_retry_resumes_approved_checkpoint_without_replaying_task(
    tmp_path, monkeypatch
):
    import lhassa.config as config_mod
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(config_mod.Config, "instance_id", "inst")
    topology = tmp_path / "org.json"
    topology.write_text(
        json.dumps(
            [{"id": "general", "role": "general", "department": None, "parent": None}]
        )
    )
    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(topology))
    config = config_mod.Config()
    repo = FlowRuntimeRepository()
    calls = []

    async def executor(goal, *, department, config, **_kwargs):
        calls.append((goal, department))
        return {"ok": True, "summary": "drafted"}

    waiting = await start_flow_run(
        _approval_flow(),
        config=config,
        executor=executor,
        idempotency_key="request-1",
        repo=repo,
    )
    assert waiting["status"] == "waiting_approval"
    [approval_row] = await db.load_pending_approvals("inst")

    # Simulate a process dying after the decision commit but before inline resume.
    resolved = await repo.resolve_gate_approval(
        "inst", approval_row["id"], approved=True, actor_id="owner"
    )
    assert resolved["run"]["status"] == "queued"

    completed = await start_flow_run(
        _approval_flow(),
        config=config,
        executor=executor,
        idempotency_key="request-1",
        repo=repo,
    )

    assert completed["status"] == "completed"
    assert [step["kind"] for step in completed["steps"]] == [
        "trigger",
        "task",
        "gate",
        "output",
    ]
    assert len(calls) == 1


def _retry_flow() -> Flow:
    return Flow(
        id="retry-flow",
        name="retry",
        nodes=[
            FlowNode("trigger", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode(
                "work",
                NodeKind.TASK,
                {
                    "brain": "general",
                    "prompt": "draft",
                    "retry": {
                        "max_attempts": 2,
                        "retry_on": ["error"],
                        "base_seconds": 0,
                    },
                },
            ),
            FlowNode("output", NodeKind.OUTPUT, {"type": "log"}),
        ],
        edges=[FlowEdge("trigger", "work"), FlowEdge("work", "output")],
    )


@pytest.mark.asyncio
async def test_retryable_failure_parks_waiting_retry_and_resume_completes(
    tmp_path, monkeypatch
):
    """The full repository round-trip: park → row is WAITING_RETRY, lease released,
    claim_run wakes it, and resume finishes through the real park_retry seam."""
    import lhassa.config as config_mod
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(config_mod.Config, "instance_id", "inst")
    topology = tmp_path / "org.json"
    topology.write_text(
        json.dumps(
            [{"id": "general", "role": "general", "department": None, "parent": None}]
        )
    )
    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(topology))
    config = config_mod.Config()
    repo = FlowRuntimeRepository()
    calls = []

    async def flaky(goal, *, department, config, **_kwargs):
        calls.append((goal, department))
        if len(calls) == 1:
            raise RuntimeError("transient")
        return {"ok": True, "summary": "drafted"}

    parked = await start_flow_run(
        _retry_flow(),
        config=config,
        executor=flaky,
        idempotency_key="retry-1",
        repo=repo,
    )
    assert parked["status"] == "waiting_retry"
    assert len(calls) == 1

    row = await repo.get_run("inst", parked["id"])
    assert row["status"] == RunStatus.WAITING_RETRY.value
    # The lease is released exactly like a completed run. CAS/lease/fencing intact.
    assert row["lease_token"] is None
    assert row["lease_owner"] is None
    assert row["next_wakeup_at"] is not None

    resumed = await resume_flow_run(
        "inst", parked["id"], config=config, executor=flaky, repo=repo
    )
    assert resumed["status"] == "completed"
    assert len(calls) == 2
    assert [step["kind"] for step in resumed["steps"]] == [
        "trigger",
        "task",
        "output",
    ]
