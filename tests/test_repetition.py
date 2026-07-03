"""Tests for lhassa.agents.repetition — the $0 repeated-think detector.

Offline: episodes are inserted directly via lhassa.core.db.append_episode against
a tmp brain DB. No LLM, no network.
"""

from dataclasses import dataclass, field

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.agents.repetition import detect_repetition, pattern_key


@dataclass
class _FakeEpisode:
    """Minimal Episode double — just enough for db.append_episode."""

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


async def _seed_episode(db, *, instance_id, despertar_por, accion, cost, when=None):
    ep = _FakeEpisode(
        id=db._new_id("ep"),
        instance_id=instance_id,
        despertar_por=despertar_por,
        accion=accion,
        costo_usd=cost,
        creado_en=when or db._now(),
    )
    await db.append_episode(ep)


@pytest.mark.asyncio
async def test_detects_repeated_costly_pattern(brain):
    for _ in range(6):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="pending_task",
            accion="think", cost=0.01,
        )
    findings = await detect_repetition("inst_test", days=7, threshold=5)
    assert len(findings) == 1
    f = findings[0]
    assert f["pattern"] == pattern_key("pending_task", "think")
    assert f["count"] == 6
    assert f["total_cost_usd"] == pytest.approx(0.06)


@pytest.mark.asyncio
async def test_below_threshold_is_not_reported(brain):
    for _ in range(3):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="pending_task",
            accion="think", cost=0.01,
        )
    findings = await detect_repetition("inst_test", days=7, threshold=5)
    assert findings == []


@pytest.mark.asyncio
async def test_zero_cost_pattern_is_not_reported(brain):
    """A pattern that never spent tokens (already $0/deterministic) is skipped."""
    for _ in range(6):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="heartbeat",
            accion="sync_repo", cost=0.0,
        )
    findings = await detect_repetition("inst_test", days=7, threshold=5)
    assert findings == []


@pytest.mark.asyncio
async def test_scoped_to_instance(brain):
    for _ in range(6):
        await _seed_episode(
            brain, instance_id="inst_other", despertar_por="pending_task",
            accion="think", cost=0.01,
        )
    findings = await detect_repetition("inst_test", days=7, threshold=5)
    assert findings == []


@pytest.mark.asyncio
async def test_old_episodes_outside_window_are_excluded(brain):
    old_ts = "2000-01-01T00:00:00+00:00"
    for _ in range(6):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="pending_task",
            accion="think", cost=0.01, when=old_ts,
        )
    findings = await detect_repetition("inst_test", days=7, threshold=5)
    assert findings == []


@pytest.mark.asyncio
async def test_never_raises_on_missing_table(brain, monkeypatch):
    """A missing/corrupt DB degrades to no findings rather than raising."""
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", "/nonexistent/path/does-not-exist.db")
    findings = await detect_repetition("inst_test", days=7, threshold=5)
    assert findings == []
