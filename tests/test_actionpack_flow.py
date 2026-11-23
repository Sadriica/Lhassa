# SPDX-License-Identifier: AGPL-3.0-or-later
"""``run_flow``: the action that puts a work-flow on Lhassa's own clock.

The load-bearing invariant is the same one ``tests/test_cli_flow.py`` asserts
for the CLI: a flow is NEVER run bare. This action must reach the engine only
through ``org.delegate.delegate_flow``, so that the hierarchy hop is logged.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def flows(tmp_path, monkeypatch):
    """A stored one-node flow, with the store pointed at ``tmp_path``."""
    import lhassa.config as config_mod
    from lhassa.flows.model import Flow

    monkeypatch.setattr(config_mod.Config, "flows_file", tmp_path / "flows.json")

    from lhassa.flows.store import FlowStore

    flow = Flow.from_dict(
        {
            "id": "daily-brief",
            "name": "Daily brief",
            "nodes": [{"id": "trigger", "kind": "trigger", "name": "start"}],
            "edges": [],
        }
    )
    FlowStore(config_mod.Config()).save(flow)
    return flow


def _run(params):
    from lhassa.actionpack._flow import run_flow_action

    return asyncio.run(run_flow_action(params))


def test_run_flow_goes_through_delegate_flow_not_the_engine(flows, monkeypatch):
    seen = {}

    async def fake_delegate(flow, **kwargs):
        seen["flow_id"] = flow.id
        return {"id": "run_1", "status": "completed", "steps": [{"node": "trigger", "ok": True}]}

    def explode(*a, **k):
        raise AssertionError("run_flow must never reach the engine directly")

    monkeypatch.setattr("lhassa.org.delegate.delegate_flow", fake_delegate)
    monkeypatch.setattr("lhassa.flows.engine.run_flow", explode)

    out = _run({"flow_id": "daily-brief"})
    assert seen["flow_id"] == "daily-brief"
    assert "completed" in out and "1 step(s)" in out and "run_1" in out


def test_run_flow_names_the_failed_steps(flows, monkeypatch):
    async def fake_delegate(flow, **kwargs):
        return {
            "id": "run_2",
            "status": "failed",
            "steps": [{"node": "trigger", "ok": True}, {"node": "task-1", "ok": False}],
        }

    monkeypatch.setattr("lhassa.org.delegate.delegate_flow", fake_delegate)
    assert "failed step(s): task-1" in _run({"flow_id": "daily-brief"})


def test_run_flow_reports_instead_of_raising(flows):
    """Every action in this package degrades to a string; this one too."""
    assert "'flow_id' param is required" in _run({})
    assert "'flow_id' param is required" in _run("not a dict")
    assert "no stored flow 'nope'" in _run({"flow_id": "nope"})


def test_run_flow_is_registered_and_allowlisted():
    """A recurring task can only be minted for an action that is both."""
    from lhassa import actionpack
    from lhassa.core.actions import ACTIONS
    from lhassa.security.allowlist import build_allowlist

    actionpack.register_all()
    assert "run_flow" in ACTIONS
    spec = build_allowlist().get("run_flow")
    assert spec is not None and not spec.requires_approval
