"""Tests for the autonomy knob (§2.6, principle #9); offline, tmp brain DB.

Covers: the streak builds only on consecutive HUMANO-origin approvals and after N
of them the action auto-approves; a denial resets the streak; the high-risk
always-approve set is NEVER auto-promoted regardless of history; non-human-origin
approvals do not build autonomy (no contagion-promotion); the streak is isolated
per (accion, actor); HITL is per-flow, never global.
"""

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.config import Config
from lhassa.security import autonomy
from lhassa.security.trust import TrustLevel


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


@pytest.fixture
def knob_config(monkeypatch):
    """Pin the threshold and the high-risk set so env can't perturb the tests."""
    monkeypatch.setattr(Config, "autonomy_threshold", 3)
    monkeypatch.setattr(Config, "always_approve_actions", {"move_money", "delete_data"})


@pytest.mark.asyncio
async def test_streak_builds_and_auto_approves_after_threshold(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    inst, accion, actor = "inst_personal", "sync_repo", "duenio"

    # Below threshold → not yet automatic.
    for expected in (1, 2):
        level = await autonomy.record_decision(
            inst, accion, actor, approved=True, origen_trust=int(TrustLevel.HUMANO)
        )
        assert level == expected
        assert await autonomy.should_auto_approve(inst, accion, actor) is False

    # The third consecutive human approval crosses the threshold.
    level = await autonomy.record_decision(
        inst, accion, actor, approved=True, origen_trust=int(TrustLevel.HUMANO)
    )
    assert level == 3
    assert await autonomy.autonomy_level(inst, accion, actor) == 3
    assert await autonomy.should_auto_approve(inst, accion, actor) is True


@pytest.mark.asyncio
async def test_denial_resets_streak(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    inst, accion, actor = "inst_personal", "sync_repo", "duenio"

    await autonomy.record_decision(inst, accion, actor, approved=True, origen_trust=2)
    await autonomy.record_decision(inst, accion, actor, approved=True, origen_trust=2)
    assert await autonomy.autonomy_level(inst, accion, actor) == 2

    # A single denial wipes the earned autonomy.
    level = await autonomy.record_decision(inst, accion, actor, approved=False)
    assert level == 0
    assert await autonomy.autonomy_level(inst, accion, actor) == 0
    assert await autonomy.should_auto_approve(inst, accion, actor) is False


@pytest.mark.asyncio
async def test_high_risk_never_auto_approves(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    inst, accion, actor = "inst_personal", "move_money", "duenio"

    # Approve it many times; far past the threshold.
    for _ in range(10):
        await autonomy.record_decision(
            inst, accion, actor, approved=True, origen_trust=int(TrustLevel.HUMANO)
        )
    assert await autonomy.autonomy_level(inst, accion, actor) >= 3
    # ...yet it is STILL never auto-approved (hard rule).
    assert await autonomy.should_auto_approve(inst, accion, actor) is False


@pytest.mark.asyncio
async def test_non_human_origin_does_not_build_autonomy(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    inst, accion, actor = "inst_personal", "sync_repo", "brain"

    # Approvals of INTERNO/MUNDO-origin actions do not earn autonomy.
    for trust in (int(TrustLevel.INTERNO), int(TrustLevel.MUNDO)):
        for _ in range(5):
            await autonomy.record_decision(
                inst, accion, actor, approved=True, origen_trust=trust
            )
    assert await autonomy.autonomy_level(inst, accion, actor) == 0
    assert await autonomy.should_auto_approve(inst, accion, actor) is False


@pytest.mark.asyncio
async def test_streak_isolated_per_action_and_actor(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    inst = "inst_personal"

    for _ in range(3):
        await autonomy.record_decision(
            inst, "sync_repo", "alice", approved=True, origen_trust=2
        )

    # Same action, different actor → its own (empty) streak.
    assert await autonomy.should_auto_approve(inst, "sync_repo", "alice") is True
    assert await autonomy.should_auto_approve(inst, "sync_repo", "bob") is False
    # Different action, same actor → its own (empty) streak.
    assert await autonomy.should_auto_approve(inst, "generate_documentation", "alice") is False


@pytest.mark.asyncio
async def test_missing_origin_trust_counts_as_human(tmp_path, monkeypatch, knob_config):
    await _init(tmp_path, monkeypatch)
    inst, accion, actor = "inst_personal", "sync_repo", "duenio"
    # When origen_trust is unknown, a human directly approving their own action is
    # the common case; it builds autonomy.
    for _ in range(3):
        await autonomy.record_decision(inst, accion, actor, approved=True)
    assert await autonomy.should_auto_approve(inst, accion, actor) is True
