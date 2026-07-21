"""Recovery behavior across durable flow orchestration boundaries."""

import json

import pytest

from lhassa.flows.durable import start_flow_run
from lhassa.flows.model import Flow, FlowEdge, FlowNode, NodeKind
from lhassa.flows.runtime import FlowRuntimeRepository


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
