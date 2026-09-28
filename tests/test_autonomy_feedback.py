"""Tests for the feedback -> autonomy-knob bridge (§2.2; the knob should MOVE
from lived feedback, not only from explicit approve/deny taps).

Seeds a tmp brain.db with episode sequences that exercise the attribution
heuristics (a concrete accion episode near a "responder" answer episode, then
the same negative/positive triggers as ``tests/test_feedback.py``) and asserts
``security.autonomy.apply_feedback``'s safety invariants: negative evidence
resets, positive evidence only reinforces an EXISTING streak by at most +1,
high-risk/protected-root actions are never touched, and a second call within
the same window is a no-op (idempotency marker).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from lhassa.config import Config
from lhassa.security import autonomy
from lhassa.security.trust import TrustLevel


@dataclass
class _FakeEpisode:
    """Minimal Episode double; just enough for db.append_episode."""

    id: str
    instance_id: str
    despertar_por: str
    accion: str
    decision: str = ""
    resultado: str = ""
    costo_usd: float = 0.0
    creado_en: str = ""
    memoria_cargada: list = field(default_factory=list)
    opciones: list = field(default_factory=list)
    dominio: str = ""


@pytest_asyncio.fixture
async def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


@pytest.fixture
def knob_config(monkeypatch):
    monkeypatch.setattr(Config, "autonomy_threshold", 3)
    monkeypatch.setattr(Config, "always_approve_actions", {"move_money", "delete_data"})


async def _seed_episode(db, *, instance_id="inst_test", accion="responder", decision="",
                         resultado="", when):
    ep = _FakeEpisode(
        id=db._new_id("ep"),
        instance_id=instance_id,
        despertar_por="telegram",
        accion=accion,
        decision=decision,
        resultado=resultado,
        creado_en=when,
    )
    await db.append_episode(ep)
    return ep.id


def _t(minute: int, hour: int = 9) -> str:
    """A timestamp anchored to "yesterday": always inside the default 7-day
    lookback regardless of when the test runs."""
    anchor = datetime.now(timezone.utc) - timedelta(days=1)
    return anchor.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


INST = "inst_test"


async def _seed_two_negative_signals_for(db, accion: str):
    """Two independent negative (correction + re-ask) signals, each attributed
    to ``accion`` via a concrete episode of that accion shortly before the
    scored "responder" answer.

    The correction/cache-hit follow-up episodes are deliberately placed just
    OUTSIDE the (symmetric, 30min) attribution window measured from the
    ``accion`` episode itself; close enough to the ORIGINAL answer to trigger
    the correction/re-ask heuristic in ``core.feedback`` (whose windows are
    measured from the answer), but far enough that the follow-up message
    itself (also a "responder" episode, independently scored) does not get
    mis-attributed to ``accion`` too via its own proximity search.
    """
    # Cluster 1; correction.
    await _seed_episode(db, accion=accion, when=_t(0, hour=9))
    await _seed_episode(
        db, accion="responder", resultado="pulled updates", when=_t(5, hour=9)
    )
    await _seed_episode(
        db, accion="responder", resultado="No, that wasn't right", when=_t(32, hour=9)
    )

    # Cluster 2; re-ask (a cache-hit re-serve of the same answer).
    await _seed_episode(db, accion=accion, when=_t(0, hour=11))
    await _seed_episode(
        db, accion="responder", resultado="pulled updates X", when=_t(5, hour=11)
    )
    await _seed_episode(
        db,
        accion="responder",
        decision="cache-hit: served past answer (sim=0.95)",
        resultado="pulled updates X",
        when=_t(35, hour=11),
    )


async def _seed_one_positive_signal_for(db, accion: str, *, hour: int = 14):
    """One "acted-on" positive signal: a responder answer followed shortly by
    a concrete ``accion`` episode."""
    await _seed_episode(
        db, accion="responder", resultado="I will pull now", when=_t(0, hour=hour)
    )
    await _seed_episode(db, accion=accion, when=_t(10, hour=hour))


# ── negative evidence resets ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_negative_signals_reset_existing_streak(brain, knob_config):
    for _ in range(2):
        await autonomy.record_decision(
            INST, "sync_repo", "duenio", approved=True, origen_trust=int(TrustLevel.HUMANO)
        )
    assert await autonomy.autonomy_level(INST, "sync_repo", "duenio") == 2

    await _seed_two_negative_signals_for(brain, "sync_repo")

    report = await autonomy.apply_feedback(INST, window_days=7)
    assert report["reset"] == ["sync_repo"]
    assert await autonomy.autonomy_level(INST, "sync_repo", "duenio") == 0


@pytest.mark.asyncio
async def test_single_negative_signal_does_not_reset(brain, knob_config):
    await autonomy.record_decision(
        INST, "sync_repo", "duenio", approved=True, origen_trust=int(TrustLevel.HUMANO)
    )
    await _seed_episode(brain, accion="sync_repo", when=_t(0, hour=9))
    await _seed_episode(brain, accion="responder", resultado="pulled", when=_t(5, hour=9))
    await _seed_episode(
        brain, accion="responder", resultado="No, wrong", when=_t(32, hour=9)
    )

    report = await autonomy.apply_feedback(INST, window_days=7)
    assert report["reset"] == []
    assert await autonomy.autonomy_level(INST, "sync_repo", "duenio") == 1


# ── positive evidence only reinforces an existing streak, by at most +1 ────


@pytest.mark.asyncio
async def test_positive_signal_reinforces_existing_streak_by_one(brain, knob_config):
    await autonomy.record_decision(
        INST, "sync_repo", "duenio", approved=True, origen_trust=int(TrustLevel.HUMANO)
    )
    assert await autonomy.autonomy_level(INST, "sync_repo", "duenio") == 1

    await _seed_one_positive_signal_for(brain, "sync_repo")

    report = await autonomy.apply_feedback(INST, window_days=7)
    assert report["reinforced"] == ["sync_repo"]
    assert await autonomy.autonomy_level(INST, "sync_repo", "duenio") == 2


@pytest.mark.asyncio
async def test_positive_signal_never_bootstraps_a_fresh_action(brain, knob_config):
    # No prior human approval at all for "sync_repo": feedback must not
    # create autonomy out of nothing.
    await _seed_one_positive_signal_for(brain, "sync_repo")

    report = await autonomy.apply_feedback(INST, window_days=7)
    assert report["reinforced"] == []
    assert await autonomy.autonomy_level(INST, "sync_repo", "duenio") == 0


# ── never touch high-risk / protected-root actions ──────────────────────────


@pytest.mark.asyncio
async def test_high_risk_action_untouched_even_with_negative_evidence(brain, knob_config):
    await autonomy.record_decision(
        INST, "move_money", "duenio", approved=True, origen_trust=int(TrustLevel.HUMANO)
    )
    await autonomy.record_decision(
        INST, "move_money", "duenio", approved=True, origen_trust=int(TrustLevel.HUMANO)
    )
    await _seed_two_negative_signals_for(brain, "move_money")

    report = await autonomy.apply_feedback(INST, window_days=7)
    assert "move_money" not in report["reset"]
    assert await autonomy.autonomy_level(INST, "move_money", "duenio") == 2


@pytest.mark.asyncio
async def test_protected_root_action_untouched_even_with_positive_evidence(brain, knob_config):
    await autonomy.record_decision(
        INST, "read_file", "duenio", approved=True, origen_trust=int(TrustLevel.HUMANO)
    )
    await _seed_one_positive_signal_for(brain, "read_file")

    report = await autonomy.apply_feedback(INST, window_days=7)
    assert "read_file" not in report["reinforced"]
    assert await autonomy.autonomy_level(INST, "read_file", "duenio") == 1


# ── idempotency ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_call_same_window_is_a_noop(brain, knob_config):
    for _ in range(2):
        await autonomy.record_decision(
            INST, "sync_repo", "duenio", approved=True, origen_trust=int(TrustLevel.HUMANO)
        )
    await _seed_two_negative_signals_for(brain, "sync_repo")

    first = await autonomy.apply_feedback(INST, window_days=7)
    assert first["reset"] == ["sync_repo"]

    second = await autonomy.apply_feedback(INST, window_days=7)
    assert second["reset"] == []
    assert second["reinforced"] == []
    assert second["skipped"] == 0
    # Still zero; the second call did not re-derive/re-apply anything.
    assert await autonomy.autonomy_level(INST, "sync_repo", "duenio") == 0


# ── never raises ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_never_raises_on_missing_instance(brain, knob_config):
    report = await autonomy.apply_feedback("no_such_instance", window_days=7)
    assert report == {"reset": [], "reinforced": [], "skipped": 0, "window_days": 7}
