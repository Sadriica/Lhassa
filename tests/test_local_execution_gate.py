"""The brain-side execution wall: heartbeat and early local runner."""

import pytest

from lhassa.config import Config
from lhassa.security.trust import Origin, TrustLevel


@pytest.fixture
async def local_gate(tmp_path, monkeypatch):
    from lhassa.core import db
    from lhassa.security import signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "ed25519")
    await db.init_db()
    return db


@pytest.mark.asyncio
async def test_step_rejects_a_signed_task_altered_after_persistence(
    local_gate, monkeypatch
):
    from lhassa._vendor import aiosqlite
    from lhassa.core import actions, step

    db = local_gate
    task_id = await db.add_task(
        "inst", "sync_repo", "allowed", params={"repo_dir": "/safe"}
    )
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            "UPDATE tasks SET objetivo = ? WHERE id = ?", ("altered", task_id)
        )
        await conn.commit()

    called = False

    async def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return "should not run"

    monkeypatch.setattr(actions, "run", fake_run)
    summary = await step.step("inst")

    assert called is False
    assert summary["rejected"] == 1
    assert (await db.get_task("inst", task_id))["estado"] == "rechazada"


@pytest.mark.asyncio
async def test_step_rejects_valid_signed_task_outside_objective_allowlist(
    local_gate, monkeypatch
):
    from lhassa.core import actions, step
    from lhassa.security import allowlist

    db = local_gate
    monkeypatch.setattr(
        allowlist, "ALLOWLIST", allowlist.build_allowlist(repos={"allowed"})
    )
    task_id = await db.add_task(
        "inst", "sync_repo", "outside", params={"repo_dir": "/outside"}
    )

    async def must_not_run(*args, **kwargs):
        raise AssertionError("allowlist-rejected action ran")

    monkeypatch.setattr(actions, "run", must_not_run)
    summary = await step.step("inst")

    assert summary["actions"] == 0
    assert summary["rejected"] == 1
    assert (await db.get_task("inst", task_id))["estado"] == "rechazada"

    # The refusal is an honest Episode, not a silent drop: it names the exact
    # allowlist reason (the out-of-scope objetivo), never a bare "no".
    episode = (await db.load_recent_episodes("inst", 1))[0]
    assert episode["decision"] == "rejected by local allowlist"
    assert "outside" in episode["resultado"]
    assert "sync_repo" in episode["resultado"]


@pytest.mark.asyncio
async def test_health_alert_sensor_crosses_gate_and_runs(local_gate, monkeypatch):
    """The health sense's 'warn' reflex (-> fixed action 'notify') must still
    cross the allowlist wall and execute; the fix must not break this sensor
    the same way ``test_legitimate_repo_sensor_crosses_gate_and_runs`` proves
    for the repo sensor.
    """
    from lhassa.core import actions, step, watchlist

    async def fake_check(instance_id):
        return [
            watchlist.Trigger(
                kind="health",
                action="health_alert",
                params={"level": "warn", "summary": "disk low"},
            )
        ]

    calls = []

    async def fake_run(action, params, episode=None):
        calls.append((action, params))
        return "notified"

    monkeypatch.setattr(watchlist, "check", fake_check)
    monkeypatch.setattr(actions, "run", fake_run)

    summary = await step.step("inst")

    assert summary["actions"] == 1
    assert summary.get("rejected", 0) == 0
    assert calls and calls[0][0] == "notify"


@pytest.mark.asyncio
async def test_run_task_now_rejects_objective_outside_allowlist(local_gate, monkeypatch):
    from lhassa.core import actions, dispatch
    from lhassa.security import allowlist

    db = local_gate
    monkeypatch.setattr(
        allowlist, "ALLOWLIST", allowlist.build_allowlist(repos={"allowed"})
    )
    task_id = await db.add_task(
        "inst", "sync_repo", "outside", params={"repo_dir": "/outside"}
    )

    async def must_not_run(*args, **kwargs):
        raise AssertionError("allowlist-rejected early action ran")

    monkeypatch.setattr(actions, "run", must_not_run)
    assert await dispatch.run_task_now(
        "inst", task_id, "sync_repo", {"repo_dir": "/outside"}
    ) is False
    assert (await db.get_task("inst", task_id))["estado"] == "rechazada"


@pytest.mark.asyncio
async def test_legitimate_repo_sensor_crosses_gate_and_runs(local_gate, monkeypatch):
    from lhassa.core import actions, step, watchlist
    from lhassa.security import allowlist

    repo = "/repos/allowed"
    monkeypatch.setattr(Config, "auto_sync_repos", repo)
    monkeypatch.setattr(
        allowlist, "ALLOWLIST", allowlist.build_allowlist(repos={repo})
    )

    async def fake_check(instance_id):
        return [
            watchlist.Trigger(
                kind="repo",
                action="repo_changed",
                objetivo=repo,
                params={"old": "a", "new": "b"},
            )
        ]

    calls = []

    async def fake_run(action, params, episode=None):
        calls.append((action, params))
        return "synced"

    monkeypatch.setattr(watchlist, "check", fake_check)
    monkeypatch.setattr(actions, "run", fake_run)

    summary = await step.step("inst")
    assert summary["actions"] == 1
    assert calls == [("sync_repo", {"repo_dir": repo})]


@pytest.mark.asyncio
async def test_earned_autonomy_prevents_mint_from_parking(local_gate, monkeypatch):
    from lhassa.core import dispatch
    from lhassa.security import allowlist, approval, autonomy

    db = local_gate
    monkeypatch.setattr(Config, "autonomy_threshold", 2)
    monkeypatch.setattr(Config, "always_approve_actions", set())
    monkeypatch.setitem(
        allowlist.ALLOWLIST,
        "sync_repo",
        allowlist.ActionSpec(
            objetivos=allowlist.WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=True,
        ),
    )
    for _ in range(2):
        await autonomy.record_decision(
            "inst",
            "sync_repo",
            "owner",
            approved=True,
            origen_trust=int(TrustLevel.HUMANO),
        )

    task_id, estado = await dispatch.mint_task(
        "inst",
        "sync_repo",
        params={"repo_dir": "/repo"},
        origin=Origin(TrustLevel.HUMANO, actor_id="owner"),
    )

    assert task_id, estado
    assert estado == "pendiente"
    assert await approval.pending("inst") == []
    assert (await db.get_task("inst", task_id))["firma"]


@pytest.mark.asyncio
async def test_low_level_pending_approval_action_cannot_bypass_hitl(
    local_gate, monkeypatch
):
    from lhassa.core import actions, step

    db = local_gate
    calls = []

    async def fake_run_command(params):
        calls.append(params)
        return "ran"

    monkeypatch.setitem(actions._REGISTRY._items, "run_command", fake_run_command)
    task_id = await db.add_task(
        "inst",
        "run_command",
        params={"command": "true"},
        origen_trust=int(TrustLevel.HUMANO),
        origen_actor="proposer",
        estado="pendiente",
    )

    summary = await step.step("inst")

    assert calls == []
    assert summary["rejected"] == 1
    assert (await db.get_task("inst", task_id))["estado"] == "rechazada"
    episode = (await db.load_recent_episodes("inst", 1))[0]
    assert "approval evidence" in episode["resultado"]


@pytest.mark.asyncio
async def test_durably_approved_task_is_released_and_executable(
    local_gate, monkeypatch
):
    from lhassa.core import actions, dispatch
    from lhassa.security import approval

    db = local_gate
    calls = []

    async def fake_run_command(params):
        calls.append(params)
        return "ran"

    monkeypatch.setitem(actions._REGISTRY._items, "run_command", fake_run_command)
    task_id, estado = await dispatch.mint_task(
        "inst",
        "run_command",
        params={"command": "true"},
        origin=Origin(TrustLevel.HUMANO, actor_id="service-proposer"),
    )
    assert estado == "esperando_aprobacion"
    ticket = (await approval.pending("inst"))[0]

    await approval.resolve(
        "inst",
        ticket["id"],
        approved=True,
        approver=approval.TrustedApprover("owner", "local-process"),
    )
    assert (await db.get_task("inst", task_id))["estado"] == "pendiente"

    assert await dispatch.run_task_now(
        "inst", task_id, "run_command", {"command": "tampered argument"}
    ) is True
    assert calls == [{"command": "true"}]
    assert (await db.get_task("inst", task_id))["estado"] == "hecha"
