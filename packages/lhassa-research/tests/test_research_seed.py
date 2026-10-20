"""The research seed + the /idea door; the geshe's aid department. Offline."""

import asyncio

import pytest


@pytest.fixture
def org_env(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(tmp_path / "org.json"), raising=False)
    monkeypatch.setattr(config_mod.Config, "flows_file", str(tmp_path / "flows.json"), raising=False)
    monkeypatch.setattr(config_mod.Config, "notes_dir", str(tmp_path / "drafts"), raising=False)
    return config_mod.Config


def test_seed_creates_aides_under_geshe_and_the_flow(org_env, tmp_path):
    from lhassa.flows.store import FlowStore
    from lhassa.org.temple import GESHE, GESHE_AIDES
    from lhassa.org.topology import load_topology
    from lhassa.research.surfaces.seed import seed_research

    out = seed_research(org_env)
    assert not out["errors"]
    assert set(out["aides_created"]) == set(GESHE_AIDES)
    topo = load_topology(org_env)
    for aide in GESHE_AIDES:
        assert topo.has(aide)
        assert aide in [c.id for c in topo.children_of(GESHE)]
        assert {"research-method", "no-ai-slop"}.issubset(
            set(topo.node(aide).skills)
        )
    flow = FlowStore(org_env).get(out["flow_id"])
    assert flow is not None
    kinds = [n.kind.value if hasattr(n.kind, "value") else str(n.kind) for n in flow.nodes]
    assert kinds.count("task") == 4
    assert "gate" in kinds and "output" in kinds
    gate = next(node for node in flow.nodes if node.kind.value == "gate")
    assert gate.config["spec"]["type"] == "research_corpus"
    for node in [n for n in flow.nodes if n.kind.value == "task"]:
        assert node.config["input_mode"] == "full"
        assert node.config["preserve_context"] is True
    assessment_flow = FlowStore(org_env).get(out["assessment_flow_id"])
    assert assessment_flow is not None
    assessment_gate = next(
        node for node in assessment_flow.nodes if node.kind.value == "gate"
    )
    assert assessment_gate.config["spec"]["type"] == "idea_assessment"
    assert len(
        [node for node in assessment_flow.nodes if node.kind.value == "task"]
    ) == 2
    # Idempotent second pass.
    again = seed_research(org_env)
    assert set(again["aides_existing"]) == set(GESHE_AIDES)
    assert again["flow_created"] is False


def test_seed_upgrades_an_existing_symbolic_research_gate(org_env):
    from lhassa.flows.store import FlowStore
    from lhassa.research.surfaces.seed import seed_research

    out = seed_research(org_env)
    store = FlowStore(org_env)
    flow = store.get(out["flow_id"])
    gate = next(node for node in flow.nodes if node.kind.value == "gate")
    gate.config["spec"] = {"type": "regex", "pattern": "## Open questions"}
    store.save(flow)

    upgraded = seed_research(org_env)
    assert upgraded["flow_updated"] is True
    repaired = FlowStore(org_env).get(out["flow_id"])
    repaired_gate = next(node for node in repaired.nodes if node.kind.value == "gate")
    assert repaired_gate.config["spec"] == {"type": "research_corpus"}


def test_idea_command_persists_task_before_ack_and_starts_early_runner(monkeypatch):
    from lhassa.notify.commands import COMMANDS

    import lhassa.core.dispatch as dispatch_mod

    minted = []
    ran = []

    async def fake_mint(instance_id, action, **kwargs):
        minted.append((instance_id, action, kwargs))
        return "task_1", "pendiente"

    async def fake_run(instance_id, task_id, action, params, **kwargs):
        ran.append((instance_id, task_id, action, params))
        return True

    monkeypatch.setattr(dispatch_mod, "mint_task", fake_mint)
    monkeypatch.setattr(dispatch_mod, "run_task_now", fake_run)

    async def _go():
        reply = await COMMANDS.get("idea")("TOON for prompts")
        await asyncio.sleep(0)  # let the created task run
        return reply

    reply = asyncio.run(_go())
    assert "geshe" in reply.lower()
    assert minted[0][1] == "research_intake"
    assert minted[0][2]["params"] == {"idea": "TOON for prompts"}
    assert ran[0][2:] == ("research_intake", {"idea": "TOON for prompts"})


def test_idea_without_text_is_usage():
    from lhassa.notify.commands import COMMANDS

    reply = asyncio.run(COMMANDS.get("idea")(""))
    assert "Usage" in reply


def test_idea_persists_versioned_custody_and_uses_durable_idempotent_run(
    org_env, tmp_path, monkeypatch
):
    from lhassa.research.surfaces.intake import research_intake

    import lhassa.research.evidence as evidence_mod
    import lhassa.org.delegate as delegate_mod
    import lhassa.org.deliver as deliver_mod
    from lhassa.research.surfaces.seed import seed_research

    seed_research(org_env)
    bundle = {
        "schema_version": 1,
        "trust": "MUNDO",
        "idea": "bounded loops",
        "captured_at": "2026-07-30T00:00:00+00:00",
        "sources": [{"id": "S1", "status": "captured", "text": "source"}],
        "bundle_sha256": "a" * 64,
    }

    async def fake_bundle(idea):
        return bundle

    delegated = []

    async def fake_delegate(flow, **kwargs):
        delegated.append((flow.id, kwargs))
        if flow.id == "flow-idea-assessment":
            criteria = {
                name: {
                    "score": 3,
                    "rationale": f"Strong specific rationale for {name}.",
                    "source_ids": (
                        ["S1"]
                        if name
                        in {
                            "theoretical_grounding",
                            "evidence_quality",
                            "novelty",
                        }
                        else []
                    ),
                }
                for name in (
                    "strategic_value",
                    "architectural_fit",
                    "theoretical_grounding",
                    "evidence_quality",
                    "testability",
                    "novelty",
                    "knowledge_reuse",
                    "feasibility",
                    "reversibility",
                )
            }
            payload = {
                "source_registry": ["S1"],
                "theory": {
                    "mechanism": "A sufficiently detailed mechanism from source [S1].",
                    "assumptions": ["The workload is representative."],
                    "source_ids": ["S1"],
                },
                "criteria": criteria,
                "uncertainties": ["Scale remains uncertain."],
            }
            import json

            return {
                "id": "run_assess",
                "steps": [{"kind": "output", "payload": json.dumps(payload)}],
            }
        return {
            "id": "run_1",
            "steps": [
                {
                    "kind": "output",
                    "payload": (
                        "## The idea in one sentence\nA claim [S1].\n"
                        "## Mechanics - down to the base\nM [S1].\n"
                        "## Evidence the authors present\n- E [S1].\n"
                        "## Sources\n- S1: https://example.test\n"
                        "## Mapping to Lhassa\nUnknown.\n"
                        "## Not yet verified by us - and the experiment that would\n"
                        "Baseline: x\nInstrument: bench\nMetric: pass\nStop rule: 5\n"
                        "## Open questions\n- Q"
                    ),
                }
            ],
        }

    delivered = []

    async def fake_deliver(message, **kwargs):
        delivered.append(message)

    monkeypatch.setattr(evidence_mod, "build_evidence_bundle", fake_bundle)
    monkeypatch.setattr(delegate_mod, "delegate_flow", fake_delegate)
    monkeypatch.setattr(deliver_mod, "deliver", fake_deliver)

    result = asyncio.run(research_intake({"idea": "bounded loops", "config": org_env}))

    assert [call[0] for call in delegated] == [
        "flow-idea-assessment",
        "flow-research-intake",
    ]
    assert delegated[0][1]["idempotency_key"] == "idea-assessment:" + "a" * 64
    assert delegated[1][1]["durable"] is True
    assert delegated[1][1]["idempotency_key"] == "idea:" + "a" * 64
    assert delegated[1][1]["inputs"]["evidence_bundle"]["sources"][0]["id"] == "S1"
    evidence_files = list((tmp_path / "drafts").glob("*.evidence.json"))
    assessment_files = list((tmp_path / "drafts").glob("*.assessment.json"))
    draft_files = list((tmp_path / "drafts").glob("*.md"))
    assert len(evidence_files) == len(assessment_files) == len(draft_files) == 1
    assert "sha256: " + "a" * 64 in draft_files[0].read_text()
    assert "earned rigorous investment" in delivered[0]
    assert "research_intake: assessment=" in result


def test_low_value_idea_is_logged_and_stops_before_full_corpus(
    org_env, tmp_path, monkeypatch
):
    from lhassa.research.surfaces.intake import research_intake

    import json

    import lhassa.research.evidence as evidence_mod
    import lhassa.org.delegate as delegate_mod
    import lhassa.org.deliver as deliver_mod
    from lhassa.core import db
    from lhassa.research.assessments import CRITERIA_WEIGHTS, EVIDENCE_BOUND
    from lhassa.research.surfaces.seed import seed_research

    seed_research(org_env)

    async def fake_bundle(idea):
        return {
            "schema_version": 1,
            "trust": "MUNDO",
            "idea": idea,
            "sources": [{"id": "S1", "status": "captured", "text": "source"}],
            "bundle_sha256": "b" * 64,
        }

    calls = []

    async def fake_delegate(flow, **kwargs):
        calls.append(flow.id)
        payload = {
            "source_registry": ["S1"],
            "theory": {
                "mechanism": "A weak but described mechanism from source [S1].",
                "assumptions": ["Representativeness is unknown."],
                "source_ids": ["S1"],
            },
            "criteria": {
                name: {
                    "score": 1,
                    "rationale": f"Weak specific rationale for {name}.",
                    "source_ids": ["S1"] if name in EVIDENCE_BOUND else [],
                }
                for name in CRITERIA_WEIGHTS
            },
            "uncertainties": ["Most behavior remains unverified."],
        }
        return {
            "id": "run_watch",
            "steps": [{"kind": "output", "payload": json.dumps(payload)}],
        }

    delivered = []

    async def fake_deliver(message, **kwargs):
        delivered.append(message)

    monkeypatch.setattr(evidence_mod, "build_evidence_bundle", fake_bundle)
    monkeypatch.setattr(delegate_mod, "delegate_flow", fake_delegate)
    monkeypatch.setattr(deliver_mod, "deliver", fake_deliver)

    result = asyncio.run(
        research_intake({"idea": "weak technology", "config": org_env})
    )

    assert calls == ["flow-idea-assessment"]
    assert "decision=watch" in result
    assert "No full corpus" in delivered[0]
    assert not list((tmp_path / "drafts").glob("*.md"))

    async def load():
        rows = await db.list_idea_assessments(org_env.instance_id)
        events = await db.list_idea_assessment_events(
            org_env.instance_id, rows[0]["id"]
        )
        return rows[0], events

    row, events = asyncio.run(load())
    assert row["status"] == "closed"
    assert row["investment_level"] == "exploration"
    assert [event["stage"] for event in events][-2:] == [
        "idea_scored",
        "investment_decided",
    ]
