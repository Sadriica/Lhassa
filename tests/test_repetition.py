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


async def _seed_episode(db, *, instance_id, despertar_por, accion, cost, when=None, decision=""):
    ep = _FakeEpisode(
        id=db._new_id("ep"),
        instance_id=instance_id,
        despertar_por=despertar_por,
        accion=accion,
        decision=decision,
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


def test_is_infrastructure_recognizes_own_machinery():
    from lhassa.agents.repetition import is_infrastructure

    # Org hops, the monitor, and scheduled maintenance beats are infra.
    assert is_infrastructure("org-deliver")
    assert is_infrastructure("org-delegate")
    assert is_infrastructure("temple-execute")
    assert is_infrastructure("templo-execute")  # legacy Spanish spelling
    assert is_infrastructure("monitor")
    assert is_infrastructure("task:temple_run")
    assert is_infrastructure("task:distill_memory")
    assert is_infrastructure("task:detect_repetition")
    assert is_infrastructure("task:send_digest")
    # Owner-driven wakes are NOT infra — real repeated work must still surface.
    assert not is_infrastructure("telegram")
    assert not is_infrastructure("task:send_reminder")
    assert not is_infrastructure("task:research_reply")
    assert not is_infrastructure("cron")


@pytest.mark.asyncio
async def test_chain_detector_ignores_temple_machinery(brain):
    """The real-world flood: the temple's own $0 hygiene chain fired every beat
    and drowned the owner in mint_script proposals. It must be ignored now."""
    from lhassa.agents.repetition import detect_mechanical_chains

    # The exact machinery seen in production: a maintenance chain under an infra
    # wake, three times, plus the org delegation/delivery hops.
    for _ in range(3):
        for acc in ("calendar_sync", "distill_memory", "detect_repetition"):
            await _seed_episode(
                brain, instance_id="inst_personal",
                despertar_por="temple-execute", accion=acc, cost=0.0,
            )
        for acc in ("delegate:lhassa→temple→synchronizer",
                    "delegate:lhassa→temple→guardian"):
            await _seed_episode(
                brain, instance_id="inst_personal",
                despertar_por="org-delegate", accion=acc, cost=0.0,
            )

    chains = await detect_mechanical_chains("inst_personal", days=7, threshold=3)
    assert chains == []  # no proposals for Lhassa's own machinery


@pytest.mark.asyncio
async def test_chain_detector_still_finds_real_owner_chains(brain):
    """Infra exclusion must not silence a genuine repeated OWNER chain."""
    from lhassa.agents.repetition import detect_mechanical_chains

    for _ in range(3):
        await _seed_episode(
            brain, instance_id="inst_personal", despertar_por="cron",
            accion="web_fetch", cost=0.01,
        )
        await _seed_episode(
            brain, instance_id="inst_personal", despertar_por="cron",
            accion="export_report", cost=0.0,
        )
    chains = await detect_mechanical_chains("inst_personal", days=7, threshold=3)
    assert any(f["chain"] == ["web_fetch", "export_report"] for f in chains)


# ── detect_recurring: recurring regardless of cost, + temporal cadence ──────────


@pytest.mark.asyncio
async def test_detect_recurring_finds_many_times_zero_cost_pattern(brain):
    """A pattern that never cost anything still shows up here (unlike detect_repetition)."""
    from lhassa.agents.repetition import detect_recurring

    for _ in range(4):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="telegram", accion="responder", cost=0.0
        )
    findings = await detect_recurring("inst_test", days=7, min_count=3)
    assert any(f["despertar_por"] == "telegram" and f["accion"] == "responder" for f in findings)
    hit = next(f for f in findings if f["accion"] == "responder")
    assert hit["count"] == 4
    assert hit["judgment"] is True  # "responder" is a chat judgment, not a tool candidate
    assert hit["cadence"] is None  # all seeded at ~the same instant — no regular gap


@pytest.mark.asyncio
async def test_detect_recurring_below_min_count_is_not_reported(brain):
    from lhassa.agents.repetition import detect_recurring

    for _ in range(2):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="telegram", accion="responder", cost=0.0
        )
    findings = await detect_recurring("inst_test", days=7, min_count=3)
    assert findings == []


@pytest.mark.asyncio
async def test_detect_recurring_flags_a_regular_cadence(brain):
    """Episodes spaced ~7 days apart, low variance, are flagged as a cadence."""
    from datetime import datetime, timedelta, timezone

    from lhassa.agents.repetition import detect_recurring

    base = datetime.now(timezone.utc) - timedelta(days=28)
    for i in range(5):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="weekly_digest", accion="think",
            cost=0.0, when=(base + timedelta(days=7 * i)).isoformat(),
        )
    findings = await detect_recurring("inst_test", days=60, min_count=3)
    hit = next(f for f in findings if f["despertar_por"] == "weekly_digest")
    assert hit["cadence"] is not None
    assert hit["cadence"]["interval_days"] == pytest.approx(7.0, abs=0.1)


@pytest.mark.asyncio
async def test_detect_recurring_irregular_gaps_are_not_a_cadence(brain):
    from datetime import datetime, timedelta, timezone

    from lhassa.agents.repetition import detect_recurring

    base = datetime.now(timezone.utc) - timedelta(days=30)
    offsets = [0, 1, 15, 16, 29]  # bursty, not a regular rhythm
    for off in offsets:
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="irregular", accion="think",
            cost=0.0, when=(base + timedelta(days=off)).isoformat(),
        )
    findings = await detect_recurring("inst_test", days=60, min_count=3)
    hit = next(f for f in findings if f["despertar_por"] == "irregular")
    assert hit["cadence"] is None


@pytest.mark.asyncio
async def test_detect_recurring_surfaces_answer_cache_hits(brain):
    """A repeated, real question already in the answer cache is its own finding."""
    from lhassa.agents.repetition import detect_recurring

    cache_id = await brain.insert_answer_cache(
        {
            "instance_id": "inst_test",
            "question": "what is the capital of France",
            "answer": "Paris",
            "embedding": [0.1, 0.2, 0.3],
            "tags": [],
        }
    )
    for _ in range(4):
        await brain.bump_answer_cache_hit("inst_test", cache_id)

    findings = await detect_recurring("inst_test", days=7, min_count=3)
    hit = next(f for f in findings if f.get("in_cache"))
    assert hit["question"] == "what is the capital of France"
    assert hit["answer"] == "Paris"
    assert hit["count"] == 5  # 1 original store + 4 bumped hits
    assert hit["judgment"] is True


@pytest.mark.asyncio
async def test_detect_recurring_never_raises_on_missing_table(brain, monkeypatch):
    import lhassa.core.db as db
    from lhassa.agents.repetition import detect_recurring

    monkeypatch.setattr(db, "DB_PATH", "/nonexistent/path/does-not-exist.db")
    findings = await detect_recurring("inst_test", days=7, min_count=3)
    assert findings == []


@pytest.mark.asyncio
async def test_detect_recurring_respects_denied_patterns(brain):
    from lhassa.agents.repetition import detect_recurring, pattern_key

    for _ in range(4):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="cron", accion="fetch_feed", cost=0.0
        )
    ap_id = await brain.add_approval(
        "inst_test", "mint_memory",
        {"accion": "mint_memory", "pattern": pattern_key("cron", "fetch_feed")},
    )
    await brain.resolve_approval("inst_test", ap_id, "rechazada")

    findings = await detect_recurring("inst_test", days=7, min_count=3)
    assert all(f["pattern"] != pattern_key("cron", "fetch_feed") for f in findings)


# ── recommend_intervention: the cheapest-fix decision table ──────────────────────


def test_recommend_intervention_picks_script_for_a_chain():
    from lhassa.agents.repetition import recommend_intervention

    finding = {
        "pattern": "cron::chain:web_fetch→send_digest",
        "despertar_por": "cron",
        "chain": ["web_fetch", "send_digest"],
        "count": 3,
    }
    rec = recommend_intervention(finding)
    assert rec["kind"] == "script"
    assert "web_fetch" in rec["payload"]["draft_script"]
    assert rec["payload"]["chain"] == ["web_fetch", "send_digest"]


def test_recommend_intervention_picks_schedule_for_a_cadence():
    from lhassa.agents.repetition import recommend_intervention

    finding = {
        "pattern": "weekly_digest::think",
        "despertar_por": "weekly_digest",
        "accion": "think",
        "count": 5,
        "total_cost_usd": 0.4,
        "judgment": False,
        "cadence": {"interval_days": 7.0, "cv": 0.02, "occurrences": 5},
        "in_cache": False,
    }
    rec = recommend_intervention(finding)
    assert rec["kind"] == "schedule"
    assert rec["payload"]["interval_days"] == 7.0
    assert "7" in rec["rationale"]


def test_recommend_intervention_picks_memory_fact_for_a_judgment():
    from lhassa.agents.repetition import recommend_intervention

    finding = {
        "pattern": "telegram::responder",
        "despertar_por": "telegram",
        "accion": "responder",
        "count": 10,
        "total_cost_usd": 0.5,
        "judgment": True,
        "cadence": None,
        "in_cache": False,
    }
    rec = recommend_intervention(finding)
    assert rec["kind"] == "memory_fact"
    assert rec["payload"]["despertar_por"] == "telegram"
    assert "×10" in rec["rationale"]


def test_recommend_intervention_picks_cache_seed_for_an_already_cached_question():
    from lhassa.agents.repetition import recommend_intervention

    finding = {
        "pattern": "answer_cache::abc123",
        "despertar_por": "telegram",
        "accion": "responder",
        "count": 5,
        "total_cost_usd": 0.0,
        "judgment": True,
        "cadence": None,
        "in_cache": True,
        "question": "what is the capital of France",
        "answer": "Paris",
    }
    rec = recommend_intervention(finding)
    assert rec["kind"] == "cache_seed"
    assert rec["payload"]["question"] == "what is the capital of France"


def test_recommend_intervention_picks_tool_for_a_costly_non_judgment_pattern():
    from lhassa.agents.repetition import recommend_intervention

    finding = {
        "pattern": "pending_task::think",
        "despertar_por": "pending_task",
        "accion": "think",
        "count": 6,
        "total_cost_usd": 0.06,
        "judgment": False,
        "cadence": None,
        "in_cache": False,
    }
    rec = recommend_intervention(finding)
    assert rec["kind"] == "tool"
    assert rec["payload"]["candidate_accion"] == "think"


def test_recommend_intervention_never_raises_on_malformed_finding():
    from lhassa.agents.repetition import recommend_intervention

    rec = recommend_intervention({})
    assert rec["kind"] == "tool"
    assert "rationale" in rec and "payload" in rec


# ── detect_routable: the self-routing consciousness ──────────────────────────


_ROUTABLE_DECISION = "think: provider=x model=y [routable:calendar_view]"


@pytest.mark.asyncio
async def test_detect_routable_finds_tagged_capability(brain):
    from lhassa.agents.repetition import detect_routable

    for _ in range(6):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="telegram", accion="responder",
            cost=0.01, decision=_ROUTABLE_DECISION,
        )

    findings = await detect_routable("inst_test", days=7, min_count=5)
    assert len(findings) == 1
    f = findings[0]
    assert f["capability"] == "calendar_view"
    assert f["count"] == 6
    assert f["total_cost_usd"] == pytest.approx(0.06)
    assert f["pattern"] == "routable::calendar_view"


@pytest.mark.asyncio
async def test_detect_routable_below_min_count_is_not_reported(brain):
    from lhassa.agents.repetition import detect_routable

    for _ in range(2):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="telegram", accion="responder",
            cost=0.0, decision=_ROUTABLE_DECISION,
        )

    findings = await detect_routable("inst_test", days=7, min_count=5)
    assert findings == []


@pytest.mark.asyncio
async def test_detect_routable_ignores_untagged_episodes(brain):
    from lhassa.agents.repetition import detect_routable

    for _ in range(6):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="telegram", accion="responder",
            cost=0.01,
        )
    findings = await detect_routable("inst_test", days=7, min_count=5)
    assert findings == []


@pytest.mark.asyncio
async def test_detect_routable_respects_denied_patterns(brain):
    from lhassa.agents.repetition import detect_routable

    for _ in range(6):
        await _seed_episode(
            brain, instance_id="inst_test", despertar_por="telegram", accion="responder",
            cost=0.01, decision=_ROUTABLE_DECISION,
        )

    ap_id = await brain.add_approval(
        "inst_test", "mint_route",
        {"accion": "mint_route", "pattern": "routable::calendar_view"},
    )
    await brain.resolve_approval("inst_test", ap_id, "rechazada")

    findings = await detect_routable("inst_test", days=7, min_count=5)
    assert findings == []


@pytest.mark.asyncio
async def test_detect_routable_never_raises_on_missing_table(brain, monkeypatch):
    import lhassa.core.db as db
    from lhassa.agents.repetition import detect_routable

    monkeypatch.setattr(db, "DB_PATH", "/nonexistent/path/does-not-exist.db")
    findings = await detect_routable("inst_test", days=7, min_count=5)
    assert findings == []


def test_recommend_intervention_picks_route_for_a_routable_finding():
    from lhassa.agents.repetition import recommend_intervention

    finding = {
        "pattern": "routable::calendar_view",
        "capability": "calendar_view",
        "count": 7,
        "total_cost_usd": 0.03,
    }
    rec = recommend_intervention(finding)
    assert rec["kind"] == "route"
    assert rec["payload"]["capability"] == "calendar_view"
    assert "calendar_view" in rec["rationale"]
    assert "×7" in rec["rationale"]
