"""Tests for lhassa.org.routing — deterministic department routing (Fase 7).

Routing is table-driven keyword matching — NO LLM. It must send the right goal to
the right department, fall back deterministically, and the general's assign_auto must
use it to pick the target (the general routes; it never executes).
"""

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.org import routing


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


# ── registry ──────────────────────────────────────────────────────────────────────

def test_registry_has_at_least_two_departments():
    names = {d.name for d in routing.list_departments()}
    assert {"optimizacion", "documentacion"} <= names
    assert len(names) >= 2


def test_register_adds_a_department():
    before = len(routing.list_departments())
    routing.register(routing.Department(name="ventas", keywords=frozenset({"quote", "sell"})))
    try:
        assert routing.get_department("ventas") is not None
        assert len(routing.list_departments()) == before + 1
        assert routing.route("write me a quote") == "ventas"
    finally:
        routing._REGISTRY.pop("ventas", None)  # keep the global registry clean


# ── deterministic routing ──────────────────────────────────────────────────────────

def test_optimization_goal_routes_to_optimizacion():
    assert routing.route("optimize the latency, it is too slow") == "optimizacion"
    assert routing.route("profile the bottleneck and improve throughput") == "optimizacion"


def test_documentation_goal_routes_to_documentacion():
    assert routing.route("write the documentation and a readme") == "documentacion"
    assert routing.route("explain this and add a changelog entry") == "documentacion"


def test_finance_goal_routes_to_finanzas():
    assert routing.route("reconcile the budget and pay the invoice") == "finanzas"


def test_unmatched_goal_falls_back():
    assert routing.route("zzz qwerty nonsense") == routing.FALLBACK_DEPARTMENT
    assert routing.route("") == routing.FALLBACK_DEPARTMENT


def test_routing_is_deterministic():
    goal = "optimize the slow heartbeat loop"
    assert routing.route(goal) == routing.route(goal) == "optimizacion"


def test_keyword_match_is_whole_word():
    # "doc" is a documentacion keyword but must not match inside "docker"
    assert routing.route("deploy the docker container") == routing.FALLBACK_DEPARTMENT


# ── assign_auto: the general routes (never executes) ──────────────────────────────

@pytest.mark.asyncio
async def test_assign_auto_routes_to_the_right_department(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import GeneralBrain
    from lhassa.org.messaging import MessageKind

    general = GeneralBrain(instance_id="inst_personal")

    opt = await general.assign_auto("optimize the slow loop", by_actor="owner")
    assert opt.kind is MessageKind.TASK_ASSIGN
    assert opt.to_instance == "dept:optimizacion"
    assert opt.payload["department"] == "optimizacion"

    doc = await general.assign_auto("write the readme docs", by_actor="owner")
    assert doc.to_instance == "dept:documentacion"


@pytest.mark.asyncio
async def test_general_assign_auto_never_executes(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import GeneralBrain

    general = GeneralBrain(instance_id="inst_personal")
    # routing + assigning only — the general has no execution method
    await general.assign_auto("optimize it", by_actor="owner")
    assert not hasattr(general, "handle")
    assert hasattr(general, "assign_auto")
