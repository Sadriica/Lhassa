from __future__ import annotations

from types import SimpleNamespace

import pytest

from lhassa import actionpack
from lhassa.actionpack._temple import TEMPLE_ACTIONS
from lhassa.core import db
from lhassa.org import integrity
from lhassa.org import temple


def config():
    return SimpleNamespace(
        instance_id="inst_integrity",
        integrity_attestation_ttl=3600,
    )


async def passing(_config, _instance_id):
    return {"passed": True, "summary": "contract holds", "evidence": {"x": 1}}


async def failing(_config, _instance_id):
    return {"passed": False, "summary": "contract drifted", "evidence": {"x": 0}}


@pytest.mark.asyncio
async def test_green_run_persists_checks_state_and_fresh_attestation(monkeypatch):
    monkeypatch.setattr(
        integrity,
        "CONTRACTS",
        (
            integrity.Contract("test.one", 1, "critical", "one", passing),
            integrity.Contract("test.two", 2, "warn", "two", passing),
        ),
    )
    result = await integrity.run_integrity(config(), trigger="test")

    assert result["status"] == "pass"
    assert result["attestation_expires_at"]
    attested = await integrity.attestation("inst_integrity")
    assert attested["valid"] is True
    checks = await db.list_conformance_checks(
        "inst_integrity", run_id=result["run_id"]
    )
    assert len(checks) == 2
    assert all(check["passed"] for check in checks)
    state = await db.get_conformance_state("inst_integrity")
    assert {row["contract_id"] for row in state} == {"test.one", "test.two"}


@pytest.mark.asyncio
async def test_warning_is_logged_and_withholds_green_attestation(monkeypatch):
    monkeypatch.setattr(
        integrity,
        "CONTRACTS",
        (
            integrity.Contract("test.ok", 1, "critical", "ok", passing),
            integrity.Contract("test.warn", 1, "warn", "warn", failing),
        ),
    )
    result = await integrity.run_integrity(config(), trigger="test")

    assert result["status"] == "warn"
    assert result["attestation_expires_at"] is None
    attested = await integrity.attestation("inst_integrity")
    assert attested["valid"] is False
    findings = await db.list_conformance_findings("inst_integrity")
    assert len(findings) == 1
    assert findings[0]["contract_id"] == "test.warn"
    assert findings[0]["evidence"] == {"x": 0}


@pytest.mark.asyncio
async def test_critical_failure_marks_run_failed(monkeypatch):
    monkeypatch.setattr(
        integrity,
        "CONTRACTS",
        (integrity.Contract("test.critical", 1, "critical", "x", failing),),
    )
    result = await integrity.run_integrity(config(), trigger="test")
    assert result["status"] == "fail"
    assert result["findings_critical"] == 1
    latest = await db.latest_conformance_run("inst_integrity")
    assert latest["status"] == "fail"


def test_integrity_action_is_closed_and_owned_by_guardian():
    assert "check_integrity" in TEMPLE_ACTIONS
    assert temple.ACTION_OWNERS["check_integrity"] == temple.GUARDIAN


@pytest.mark.asyncio
async def test_integrity_action_returns_compact_result(monkeypatch):
    actionpack.register_all()

    async def fake_integrity(_config, *, trigger):
        assert trigger == "test-action"
        return {
            "run_id": "conf_action",
            "status": "warn",
            "findings_critical": 0,
            "findings_warn": 2,
        }

    monkeypatch.setattr(integrity, "run_integrity", fake_integrity)
    from lhassa.core import actions

    raw = await actions.run(
        "check_integrity",
        {"config": config(), "trigger": "test-action"},
    )
    import json

    result = json.loads(raw)
    assert result == {
        "findings_critical": 0,
        "findings_warn": 2,
        "run_id": "conf_action",
        "status": "warn",
    }


@pytest.mark.asyncio
async def test_research_contract_detects_non_contiguous_projection():
    await db.init_db()
    row = await db.create_idea_assessment(
        "inst_integrity",
        idea="test",
        evidence_digest="f" * 64,
        evidence_path="/tmp/evidence.json",
    )
    await db.transition_idea_assessment(
        "inst_integrity",
        row["id"],
        stage="bad_skip",
        to_status="researched",
        updates={"draft_path": "/tmp/draft.md", "decision": "watch"},
    )
    result = await integrity._research_event_chain(config(), "inst_integrity")
    assert result["passed"] is False
    assert any(
        item["reason"] == "deep research without earned disposition"
        for item in result["evidence"]["violations"]
    )


@pytest.mark.asyncio
async def test_watcher_runs_integrity_on_its_own_cadence(monkeypatch):
    from lhassa.org import watcher
    from lhassa.org import topology as topo_mod
    from lhassa.org.topology import BrainNode, Topology

    watcher._STATE["prev"] = None
    watcher._STATE["last_integrity_at"] = 0.0
    monkeypatch.setattr(
        watcher,
        "gather_signals",
        lambda _config: {
            "episodes": [],
            "health": {"worst_level": "info"},
            "flows": [],
            "approvals": [],
            "failures": [],
        },
    )
    monkeypatch.setattr(watcher, "_run_statuses", lambda: {})
    monkeypatch.setattr(
        topo_mod,
        "load_topology",
        lambda _config=None: Topology(
            [
                BrainNode(id="lhassa", role="administrator"),
                BrainNode(
                    id="temple",
                    role="internal-operations",
                    department="temple",
                    parent="lhassa",
                ),
                BrainNode(
                    id="guardian",
                    role="monk",
                    department="temple",
                    parent="temple",
                ),
            ]
        ),
    )

    async def fake_integrity(_config, *, trigger):
        assert trigger == "watcher"
        return {
            "run_id": "conf_test",
            "status": "fail",
            "findings_critical": 1,
            "findings_warn": 0,
        }

    monkeypatch.setattr(integrity, "run_integrity", fake_integrity)
    reacted = []

    async def fake_react(events, **kwargs):
        reacted.extend(events)
        return {}

    monkeypatch.setattr(watcher, "react", fake_react)
    cfg = SimpleNamespace(
        instance_id="inst_integrity",
        integrity_enabled=True,
        integrity_interval=3600,
        monitor_idle_interval=300,
        monitor_active_interval=15,
        monitor_department="guardian",
    )
    await watcher.watch_loop(cfg, once=True)
    assert reacted[0]["kind"] == "conformance_failed"
    assert reacted[0]["level"] == "alert"
    assert "conf_test" in reacted[0]["summary"]

    rows = await db.load_recent_episodes("inst_integrity", n=100)
    execution = [
        row
        for row in rows
        if row["despertar_por"] == "temple-execute"
        and row["accion"] == "check_integrity"
    ]
    assert execution
    assert execution[0]["dominio"] == "guardian"
