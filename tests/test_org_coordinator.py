"""Tests for lhassa.org.coordinator; the two-brain core (Fase 7)."""

import pytest
import pytest_asyncio  # noqa: F401


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


# ── two brains: general assigns, department executes, general collects ──────────

@pytest.mark.asyncio
async def test_two_brain_assign_handle_collect(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import GeneralBrain, DepartmentBrain
    from lhassa.org.messaging import MessageKind

    general = GeneralBrain(instance_id="inst_personal")
    sales = DepartmentBrain(department="sales", instance_id="inst_personal")

    # who actually ran the work; proves the department side executes, not general
    ran_on = {}

    def executor(goal, *, department):
        ran_on["department"] = department
        ran_on["goal"] = goal
        return {"quote": 42}

    assign = await general.assign(
        "draft a quote", to_department="sales", by_actor="owner"
    )
    assert assign.kind is MessageKind.TASK_ASSIGN
    assert assign.to_instance == "dept:sales"

    # the department picks up its inbox and handles it
    from lhassa.org import messaging
    inbox = await messaging.inbox("dept:sales", instance_id="inst_personal")
    assert len(inbox) == 1

    result = await sales.handle(inbox[0], executor=executor)
    assert result.kind is MessageKind.TASK_RESULT
    assert result.corr_id == assign.corr_id
    assert result.payload["result"] == {"quote": 42}

    # the executor ran on the DEPARTMENT side
    assert ran_on == {"department": "sales", "goal": "draft a quote"}

    # the general collects the correlated result
    collected = await general.collect(assign.corr_id)
    assert len(collected) == 1
    assert collected[0].payload["result"] == {"quote": 42}


@pytest.mark.asyncio
async def test_general_never_executes_the_work(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import GeneralBrain

    general = GeneralBrain(instance_id="inst_personal")
    # GeneralBrain has no execution method at all; it only routes.
    assert not hasattr(general, "handle")
    assert hasattr(general, "assign")
    assert hasattr(general, "collect")


@pytest.mark.asyncio
async def test_async_executor_supported(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import GeneralBrain, DepartmentBrain

    general = GeneralBrain(instance_id="inst_personal")
    eng = DepartmentBrain(department="eng", instance_id="inst_personal")

    async def executor(goal, *, department):
        return {"built": goal}

    assign = await general.assign("ship it", to_department="eng", by_actor="owner")
    inbox = await _inbox("dept:eng")
    result = await eng.handle(inbox[0], executor=executor)
    assert result.payload["result"] == {"built": "ship it"}
    assert result.corr_id == assign.corr_id


async def _inbox(to):
    from lhassa.org import messaging
    return await messaging.inbox(to, instance_id="inst_personal")


# ── RBAC enforcement on the brains ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_department_cannot_assign(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import GeneralBrain
    from lhassa.org import rbac

    # a department role may not assign; verified at the matrix and brain level
    assert not rbac.can("department", "assign", "task")
    # GeneralBrain.assign requires the general role (it is the only assigner)
    general = GeneralBrain(instance_id="inst_personal")
    assert general.role == "general"


@pytest.mark.asyncio
async def test_handle_rejects_non_assign_message(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import DepartmentBrain
    from lhassa.org import messaging
    from lhassa.org.messaging import MessageKind

    dept = DepartmentBrain(department="sales", instance_id="inst_personal")
    bogus = messaging.new_message(
        MessageKind.STATUS_REPORT, from_instance="general", to_instance="dept:sales"
    )
    with pytest.raises(ValueError):
        await dept.handle(bogus)


# ── audit plane: an episode (despertar_por="org") is written ────────────────────

@pytest.mark.asyncio
async def test_handle_writes_org_episode(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import GeneralBrain, DepartmentBrain

    general = GeneralBrain(instance_id="inst_personal")
    sales = DepartmentBrain(department="sales", instance_id="inst_personal")

    await general.assign("x", to_department="sales", by_actor="owner")
    inbox = await _inbox("dept:sales")
    await sales.handle(inbox[0])

    eps = await db.load_recent_episodes("inst_personal")
    assert any(e["despertar_por"] == "org" for e in eps)


# ── multi-person approval: proposer ≠ approver for high-risk ─────────────────────

@pytest.mark.asyncio
async def test_high_risk_self_approval_rejected(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import GeneralBrain, DepartmentBrain, SelfApprovalError

    general = GeneralBrain(instance_id="inst_personal")
    finance = DepartmentBrain(department="finance", instance_id="inst_personal")

    ran = {"n": 0}

    def executor(goal, *, department):
        ran["n"] += 1
        return {"ok": True}

    assign = await general.assign(
        "wire the funds", to_department="finance", by_actor="owner", high_risk=True
    )
    inbox = await _inbox("dept:finance")

    # same actor approving their own high-risk proposal → rejected, no execution
    with pytest.raises(SelfApprovalError):
        await finance.handle(inbox[0], executor=executor, approver="owner")
    assert ran["n"] == 0

    # missing approver → also rejected
    with pytest.raises(SelfApprovalError):
        await finance.handle(inbox[0], executor=executor)
    assert ran["n"] == 0

    # a DISTINCT approver lets it proceed
    result = await finance.handle(inbox[0], executor=executor, approver="manager")
    assert ran["n"] == 1
    assert result.corr_id == assign.corr_id


@pytest.mark.asyncio
async def test_high_risk_distinct_approver_resolves_approval(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import GeneralBrain, DepartmentBrain

    general = GeneralBrain(instance_id="inst_personal")
    finance = DepartmentBrain(department="finance", instance_id="inst_personal")

    await general.assign(
        "wire the funds", to_department="finance", by_actor="owner", high_risk=True
    )
    # an approval was parked at assign-time
    pend = await db.load_pending_approvals("inst_personal")
    assert len(pend) == 1

    inbox = await _inbox("dept:finance")
    await finance.handle(inbox[0], approver="manager")

    # the parked approval was resolved by the distinct approver
    assert await db.load_pending_approvals("inst_personal") == []


@pytest.mark.asyncio
async def test_low_risk_needs_no_approver(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org.coordinator import GeneralBrain, DepartmentBrain

    general = GeneralBrain(instance_id="inst_personal")
    sales = DepartmentBrain(department="sales", instance_id="inst_personal")

    await general.assign("draft email", to_department="sales", by_actor="owner")
    inbox = await _inbox("dept:sales")
    result = await sales.handle(inbox[0])  # no approver needed
    assert result.payload["result"]["ok"] is True
