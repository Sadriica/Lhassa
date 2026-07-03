"""Tests for lhassa/actionpack/_org.py — org_run (Etapa 3 real delegation).

Fully offline: ``org.coordinator.run_goal`` is monkeypatched so no real topology/
DB/model is touched — these tests only verify org_run's own plumbing (required
param, param pass-through, never-raise on failure).
"""

import pytest
import pytest_asyncio  # noqa: F401


@pytest.mark.asyncio
async def test_org_run_requires_goal():
    from lhassa.actionpack._org import org_run

    result = await org_run({})
    assert "goal" in result
    assert "required" in result


@pytest.mark.asyncio
async def test_org_run_happy_path(monkeypatch):
    import lhassa.org.coordinator as coordinator_mod

    calls = []

    async def fake_run_goal(goal, *, by_actor, high_risk, instance_id, params=None):
        calls.append((goal, by_actor, high_risk, instance_id, params))
        return {
            "goal": goal,
            "department": "optimization",
            "leaf": "dept:optimization",
            "result": {"summary": "done"},
        }

    monkeypatch.setattr(coordinator_mod, "run_goal", fake_run_goal)

    from lhassa.actionpack._org import org_run

    result = await org_run({"goal": "speed things up"})
    assert "org_run" in result
    assert "optimization" in result
    assert len(calls) == 1
    assert calls[0][0] == "speed things up"


@pytest.mark.asyncio
async def test_org_run_passes_multi_and_department_hint(monkeypatch):
    import lhassa.org.coordinator as coordinator_mod

    captured = {}

    async def fake_run_goal(goal, *, by_actor, high_risk, instance_id, params=None):
        captured["params"] = params
        return {"goal": goal, "department": ["a", "b"], "result": "merged", "multi": True}

    monkeypatch.setattr(coordinator_mod, "run_goal", fake_run_goal)

    from lhassa.actionpack._org import org_run

    result = await org_run({"goal": "x", "multi": True, "department": "hint"})
    assert captured["params"] == {"multi": True, "department": "hint"}
    assert "(multi)" in result


@pytest.mark.asyncio
async def test_org_run_never_raises_on_self_approval_error(monkeypatch):
    import lhassa.org.coordinator as coordinator_mod
    from lhassa.org.coordinator import SelfApprovalError

    async def fake_run_goal(goal, *, by_actor, high_risk, instance_id, params=None):
        raise SelfApprovalError("needs a distinct approver")

    monkeypatch.setattr(coordinator_mod, "run_goal", fake_run_goal)

    from lhassa.actionpack._org import org_run

    result = await org_run({"goal": "wire the funds", "high_risk": True})
    assert isinstance(result, str)
    assert "SelfApprovalError" in result


def test_org_run_registered_in_actionpack():
    from lhassa import actionpack
    actionpack.register_all()
    from lhassa.core.actions import ACTIONS
    assert "org_run" in ACTIONS
