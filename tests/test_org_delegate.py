"""Tests for lhassa.org.delegate — the deterministic org delegation seam.

Offline + $0: the owning department is derived from the topology alone (no model),
the flow runs through the OFFLINE org executor (LHASSA_ORG_LIVE left OFF), and the
delegation Episode is logged to a tmp brain DB. Covers:

  * ``owning_department`` resolves the interns department for a CV-style flow whose
    task nodes are pinned to a brain under that department, and falls back to the
    root admin (Lhassa) when nothing resolves;
  * ``command_path`` reads the root→…→dept chain off the topology tree;
  * ``delegate_flow`` logs an ``org-delegate`` Episode and returns the flow record
    annotated with ``record["delegation"].chain`` = the root→…→dept path;
  * the degrade path — a broken topology still runs the flow and returns a record
    (with a minimal/empty chain), never raising.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.flows.model import Flow, FlowEdge, FlowNode, NodeKind
from lhassa.org import delegate
from lhassa.org.topology import BrainNode, Topology


# ── fixtures / builders ───────────────────────────────────────────────────────────
def _topology() -> Topology:
    """lhassa (root admin) → general → interns (a department leaf)."""
    return Topology(
        [
            BrainNode(id="lhassa", role="administrator"),
            BrainNode(id="general", role="general", parent="lhassa"),
            BrainNode(id="interns", role="department", department="interns", parent="general"),
        ]
    )


def _cv_flow() -> Flow:
    """A CV-style flow: task nodes PINNED to the interns brain."""
    return Flow(
        id="cv",
        name="cv",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w1", NodeKind.TASK, {"brain": "interns", "prompt": "analyse {input}"}),
            FlowNode("w2", NodeKind.TASK, {"brain": "interns", "prompt": "tailor {input}"}),
            FlowNode("o", NodeKind.OUTPUT, {"type": "log", "target": "audit"}),
        ],
        edges=[FlowEdge("t", "w1"), FlowEdge("w1", "w2"), FlowEdge("w2", "o")],
    )


def _unpinned_flow() -> Flow:
    """A flow whose task auto-routes (no pinned brain) → owns no fixed department."""
    return Flow(
        id="x",
        name="x",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"skill": "optimization", "prompt": "go"}),
        ],
        edges=[FlowEdge("t", "w")],
    )


def _cfg(tmp_path):
    topo = tmp_path / "org.json"
    topo.write_text(
        '[{"id":"general","role":"general","department":null,"parent":null},'
        '{"id":"interns","role":"department","department":"interns","parent":"general"}]',
        encoding="utf-8",
    )
    return SimpleNamespace(
        instance_id="inst_personal",
        org_topology_file=str(topo),
        flows_file=str(tmp_path / "flows.json"),
        skills_dir=str(tmp_path / "skills"),
        health_dir=str(tmp_path / "health"),
        root_admin_id="lhassa",
        context_token_budget=2000,
    )


# ── owning_department (deterministic, no LLM) ─────────────────────────────────────
def test_owning_department_resolves_interns():
    assert delegate.owning_department(_cv_flow(), _topology()) == "interns"


def test_owning_department_falls_back_to_root_when_unresolvable():
    # No pinned brains → nothing to anchor on → fall back to the root admin (Lhassa).
    assert delegate.owning_department(_unpinned_flow(), _topology()) == "lhassa"


def test_owning_department_unknown_pin_falls_back_to_root():
    flow = Flow(
        id="u",
        name="u",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"brain": "does-not-exist", "prompt": "go"}),
        ],
        edges=[FlowEdge("t", "w")],
    )
    assert delegate.owning_department(flow, _topology()) == "lhassa"


def test_command_path_is_root_to_department():
    assert delegate.command_path(_topology(), "interns") == ["lhassa", "general", "interns"]
    # An unlocatable department degrades to just the root.
    assert delegate.command_path(_topology(), "ghost") == ["lhassa"]


# ── delegate_flow: logs the episode + annotates the chain, runs OFFLINE ($0) ──────
@pytest.mark.asyncio
async def test_delegate_flow_logs_episode_and_annotates_chain(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)  # stay OFFLINE → no model
    await db.init_db()

    cfg = _cfg(tmp_path)
    record = await delegate.delegate_flow(
        _cv_flow(), inputs={"summary": "seed"}, config=cfg, base_cv=""
    )

    # The flow ran offline and completed.
    assert record["status"] == "completed"
    task = next(s for s in record["steps"] if s["kind"] == "task")
    assert "[offline]" in task["result"]["summary"]

    # The record is annotated with the deterministic delegation chain.
    assert record["delegation"]["department"] == "interns"
    assert record["delegation"]["chain"] == ["lhassa", "general", "interns"]

    # One LLM-free ($0) delegation Episode was written to the audit plane — the same
    # plane org/monitor.py reads, so the monitor sees the whole chain.
    from lhassa.org.monitor import gather_signals

    eps = gather_signals(cfg, instance_id="inst_personal")["episodes"]
    delegated = [e for e in eps if e["despertar_por"] == "org-delegate"]
    assert delegated, "expected an org-delegate episode"
    ep = delegated[0]
    assert ep["accion"] == "delegate:lhassa→general→interns"
    assert "down to interns" in ep["decision"]
    assert float(ep["costo_usd"]) == 0.0


# ── degrade path: a broken topology still runs the flow ───────────────────────────
@pytest.mark.asyncio
async def test_delegate_flow_degrades_when_topology_broken(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.org import topology as topo_mod

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)
    await db.init_db()

    def _boom(_config=None):
        raise RuntimeError("topology unavailable")

    monkeypatch.setattr(topo_mod, "load_topology", _boom)

    # Governance must NEVER block the work: the flow still runs, record still returned.
    record = await delegate.delegate_flow(
        _cv_flow(), inputs={"summary": "seed"}, config=_cfg(tmp_path), base_cv=""
    )
    assert record["status"] == "completed"
    assert record["delegation"] == {"chain": [], "department": ""}
