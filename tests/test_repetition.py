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


# ── exclusions: a judgment is never a candidate (the five real denied shapes) ──────


@pytest.mark.asyncio
async def test_judgment_patterns_are_excluded(brain):
    """The detector's five real (denied) proposals can never come back: the chat
    'responder' and the org 'dept:X think' shapes are judgments by definition."""
    from lhassa.agents.repetition import detect_repetition

    for accion in (
        "responder",
        "dept:researcher think",
        "dept:critic think",
        "dept:ats-evaluator think",
        "dept:jd-analyst think",
    ):
        for _ in range(6):
            await _seed_episode(
                brain, instance_id="inst_personal", despertar_por="org", accion=accion, cost=0.05
            )
    found = await detect_repetition("inst_personal", days=7, threshold=5)
    assert found == []


def test_is_excluded_covers_think_decisions():
    from lhassa.agents.repetition import is_excluded

    assert is_excluded("responder")
    assert is_excluded("dept:interns think")
    assert is_excluded("algo_nuevo", decision="think: provider=x model=y")
    assert not is_excluded("sync_repo")
    assert not is_excluded("send_digest", decision="send_digest")


@pytest.mark.asyncio
async def test_denied_pattern_is_never_reproposed(brain):
    """A rechazada mint_action approval keeps that exact pattern out of findings."""
    from lhassa.agents.repetition import detect_repetition, pattern_key

    for _ in range(6):
        await _seed_episode(
            brain, instance_id="inst_personal", despertar_por="cron", accion="fetch_feed", cost=0.02
        )
    ap_id = await brain.add_approval(
        "inst_personal",
        "mint_action",
        {"accion": "mint_action", "pattern": pattern_key("cron", "fetch_feed")},
    )
    await brain.resolve_approval("inst_personal", ap_id, "rechazada")

    found = await detect_repetition("inst_personal", days=7, threshold=5)
    assert all(f["pattern"] != pattern_key("cron", "fetch_feed") for f in found)


# ── mechanical chains → PTC drafts ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mechanical_chain_is_detected_with_a_draft(brain):
    from lhassa.agents.repetition import detect_mechanical_chains, draft_script_for_chain

    # The same two-step chain under one trigger, three times, non-think decisions.
    for _ in range(3):
        await _seed_episode(
            brain, instance_id="inst_personal", despertar_por="cron", accion="web_fetch", cost=0.0
        )
        await _seed_episode(
            brain, instance_id="inst_personal", despertar_por="cron", accion="send_digest", cost=0.0
        )
    chains = await detect_mechanical_chains("inst_personal", days=7, threshold=3)
    assert any(f["chain"] == ["web_fetch", "send_digest"] for f in chains)

    draft = draft_script_for_chain(["web_fetch", "send_digest"])
    assert "import lhassa_actions as la" in draft
    assert "la.web_fetch()" in draft and "la.send_digest()" in draft
    assert "TODO" in draft


@pytest.mark.asyncio
async def test_chain_needs_distinct_actions_and_threshold(brain):
    from lhassa.agents.repetition import detect_mechanical_chains

    # A monotone run (one action repeated) is NOT a chain.
    for _ in range(8):
        await _seed_episode(
            brain, instance_id="inst_personal", despertar_por="cron", accion="ping_url", cost=0.0
        )
    chains = await detect_mechanical_chains("inst_personal", days=7, threshold=3)
    assert chains == []
