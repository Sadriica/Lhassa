"""Tests for lhassa.agents.repetition; the $0 repeated-think detector.

Offline: episodes are inserted directly via lhassa.core.db.append_episode against
a tmp brain DB. No LLM, no network.
"""

from dataclasses import dataclass, field

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.agents.repetition import detect_repetition, effective_threshold, pattern_key


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
    # Owner-driven wakes are NOT infra; real repeated work must still surface.
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
    assert hit["cadence"] is None  # all seeded at ~the same instant; no regular gap


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


# ── effective_threshold: adaptive sensitivity for a young/small population ──────


def test_effective_threshold_relaxes_toward_floor_for_tiny_population():
    assert effective_threshold(5, 0) == 2  # default floor
    assert effective_threshold(5, 1) == 2
    assert effective_threshold(5, 3) == 2


def test_effective_threshold_caps_at_base_for_large_population():
    # population >= base * base (5 * 5 = 25) reaches the full strict base.
    assert effective_threshold(5, 25) == 5
    assert effective_threshold(5, 100) == 5
    assert effective_threshold(5, 1000) == 5


def test_effective_threshold_scales_between_floor_and_base():
    assert effective_threshold(5, 10) == 2  # ceil(10/5)=2
    assert effective_threshold(5, 15) == 3  # ceil(15/5)=3
    assert effective_threshold(5, 20) == 4  # ceil(20/5)=4


def test_effective_threshold_is_monotonic_in_population():
    prev = effective_threshold(5, 0)
    for population in range(0, 60):
        cur = effective_threshold(5, population)
        assert cur >= prev
        prev = cur


def test_effective_threshold_respects_custom_floor():
    assert effective_threshold(5, 0, floor=1) == 1
    assert effective_threshold(5, 0, floor=4) == 4
    # floor can never exceed base, even if a caller passes floor > base.
    assert effective_threshold(3, 0, floor=10) == 3


def test_effective_threshold_never_raises_on_bad_input():
    assert effective_threshold(0, 5) == 1  # base < 1 degrades to 1
    assert effective_threshold(-5, 5) == 1
    assert effective_threshold(5, -3) == 2  # negative population degrades to 0


# ── adaptive sensitivity wired into the detectors ────────────────────────────────


@pytest.mark.asyncio
async def test_detect_repetition_fires_with_few_repeats_in_a_tiny_population(brain):
    """No explicit threshold: DEFAULT_THRESHOLD=5 would never fire on 3 repeats,
    but a young/tiny window (population==3, nothing else in the log) relaxes the
    bar toward the floor; this is the whole point of item 2.1."""
    for _ in range(3):
        await _seed_episode(
            brain, instance_id="inst_young", despertar_por="pending_task",
            accion="think", cost=0.01,
        )
    findings = await detect_repetition("inst_young", days=7)
    assert len(findings) == 1
    f = findings[0]
    assert f["count"] == 3
    assert f["threshold_used"] == effective_threshold(5, 3)
    assert f["threshold_used"] < 5


@pytest.mark.asyncio
async def test_detect_repetition_does_not_fire_with_same_repeats_in_a_large_population(brain):
    """Same 3 repeats of the target pattern, but a large window (many other
    episodes) keeps the effective threshold at the strict base; no noise for a
    mature brain."""
    for _ in range(3):
        await _seed_episode(
            brain, instance_id="inst_mature", despertar_por="pending_task",
            accion="think", cost=0.01,
        )
    for i in range(27):
        await _seed_episode(
            brain, instance_id="inst_mature", despertar_por=f"filler_{i}",
            accion="noop", cost=0.0,
        )
    findings = await detect_repetition("inst_mature", days=7)
    assert findings == []


@pytest.mark.asyncio
async def test_detect_repetition_explicit_threshold_is_never_adapted(brain):
    """An explicit `threshold=` argument wins unchanged even in a tiny population
    that would otherwise relax the bar; backward compatible with existing callers."""
    for _ in range(3):
        await _seed_episode(
            brain, instance_id="inst_explicit", despertar_por="pending_task",
            accion="think", cost=0.01,
        )
    findings = await detect_repetition("inst_explicit", days=7, threshold=5)
    assert findings == []  # 3 < explicit 5, no adaptation applied

    findings2 = await detect_repetition("inst_explicit", days=7, threshold=3)
    assert len(findings2) == 1
    assert findings2[0]["threshold_used"] == 3  # explicit value echoed back


@pytest.mark.asyncio
async def test_detect_recurring_adaptive_threshold_used_and_relaxes(brain):
    """DEFAULT_RECURRING_THRESHOLD=3 with floor 2: a tiny population (only these
    2 episodes) relaxes the bar to 2, so 2 repeats now surfaces a finding that a
    static base=3 never would."""
    from lhassa.agents.repetition import detect_recurring

    for _ in range(2):
        await _seed_episode(
            brain, instance_id="inst_recurring_young", despertar_por="cron",
            accion="fetch_feed", cost=0.0,
        )
    findings = await detect_recurring("inst_recurring_young", days=7)
    hit = next(f for f in findings if f["accion"] == "fetch_feed")
    assert hit["count"] == 2
    assert hit["threshold_used"] == effective_threshold(3, 2)
    assert hit["threshold_used"] == 2


@pytest.mark.asyncio
async def test_detect_recurring_explicit_min_count_is_never_adapted(brain):
    from lhassa.agents.repetition import detect_recurring

    for _ in range(2):
        await _seed_episode(
            brain, instance_id="inst_recurring_explicit", despertar_por="cron",
            accion="fetch_feed", cost=0.0,
        )
    findings = await detect_recurring("inst_recurring_explicit", days=7, min_count=3)
    assert findings == []  # 2 < explicit 3, no adaptation applied


@pytest.mark.asyncio
async def test_detect_routable_adaptive_threshold_used_and_relaxes(brain):
    from lhassa.agents.repetition import detect_routable

    for _ in range(2):
        await _seed_episode(
            brain, instance_id="inst_routable_young", despertar_por="telegram",
            accion="responder", cost=0.0, decision=_ROUTABLE_DECISION,
        )
    findings = await detect_routable("inst_routable_young", days=7)
    assert len(findings) == 1
    f = findings[0]
    assert f["count"] == 2
    assert f["threshold_used"] == effective_threshold(5, 2)
    assert f["threshold_used"] < 5


@pytest.mark.asyncio
async def test_detect_routable_explicit_min_count_is_never_adapted(brain):
    from lhassa.agents.repetition import detect_routable

    for _ in range(2):
        await _seed_episode(
            brain, instance_id="inst_routable_explicit", despertar_por="telegram",
            accion="responder", cost=0.0, decision=_ROUTABLE_DECISION,
        )
    findings = await detect_routable("inst_routable_explicit", days=7, min_count=5)
    assert findings == []  # 2 < explicit 5, no adaptation applied


# ══ chain detector: approval-gated actions, bookkeeping rows, overlap, rotations ══


async def _seq(db, actions, *, despertar_por="cron", start_day=0, step_minutes=1,
               decision="", instance_id="inst_chain"):
    """Insert ``actions`` as consecutive episodes, ``step_minutes`` apart, from
    ``start_day`` days ago (newest-last so ordering is deterministic)."""
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc) - timedelta(days=start_day, hours=1)
    for i, accion in enumerate(actions):
        await _seed_episode(
            db, instance_id=instance_id, despertar_por=despertar_por, accion=accion,
            cost=0.0, decision=decision, when=(base + timedelta(minutes=i * step_minutes)).isoformat(),
        )


@pytest.mark.asyncio
async def test_a_chain_with_an_approval_gated_action_is_never_proposed(brain):
    """run_command requires approval; ptc can never run it, so no chain contains it."""
    from lhassa.agents.repetition import detect_mechanical_chains

    await _seq(brain, ["run_command", "read_file"] * 4)
    assert await detect_mechanical_chains("inst_chain", days=7, threshold=2) == []


@pytest.mark.asyncio
async def test_a_gated_action_is_a_barrier_not_a_bridge(brain):
    """x, GATED, y repeated must not become the chain x -> y."""
    from lhassa.agents.repetition import detect_mechanical_chains

    await _seq(brain, ["web_fetch", "run_command", "export_report"] * 4)
    chains = await detect_mechanical_chains("inst_chain", days=7, threshold=2)
    assert not any("run_command" in f["chain"] for f in chains)
    assert not any(f["chain"] == ["web_fetch", "export_report"] for f in chains)


@pytest.mark.asyncio
async def test_capability_use_bookkeeping_episodes_are_not_work(brain):
    from lhassa.agents.repetition import detect_mechanical_chains

    await _seq(brain, ["web_fetch", "export_report"] * 4,
               decision="capability use recorded from `lhassa memory add`")
    assert await detect_mechanical_chains("inst_chain", days=7, threshold=2) == []


@pytest.mark.asyncio
async def test_occurrences_are_counted_without_overlap(brain):
    """a b a b a b is THREE occurrences of the cycle, not five shifted windows."""
    from lhassa.agents.repetition import detect_mechanical_chains

    await _seq(brain, ["web_fetch", "export_report"] * 3)
    (f,) = await detect_mechanical_chains("inst_chain", days=7, threshold=2)
    assert f["count"] == 3
    assert f["chain"] == ["web_fetch", "export_report"]  # as it first ran


@pytest.mark.asyncio
async def test_rotations_and_repetitions_collapse_into_one_chain(brain):
    """The measured flood: 10 proposals that were rotations/substrings of one alternation."""
    from lhassa.agents.repetition import detect_mechanical_chains

    await _seq(brain, ["web_fetch", "export_report"] * 6)
    found = await detect_mechanical_chains("inst_chain", days=7, threshold=2, max_len=4)
    assert len(found) == 1
    assert found[0]["pattern"] == "cron::chain:export_report→web_fetch"  # minimal rotation


@pytest.mark.asyncio
async def test_a_subchain_of_a_longer_qualifying_chain_is_dropped(brain):
    from lhassa.agents.repetition import detect_mechanical_chains

    await _seq(brain, ["ping_url", "web_fetch", "export_report"] * 5)
    found = await detect_mechanical_chains("inst_chain", days=7, threshold=3)
    assert [sorted(f["chain"]) for f in found] == [["export_report", "ping_url", "web_fetch"]]


@pytest.mark.asyncio
async def test_days_seen_counts_distinct_days(brain):
    from lhassa.agents.repetition import detect_mechanical_chains

    await _seq(brain, ["web_fetch", "export_report"] * 2, start_day=0)
    await _seq(brain, ["web_fetch", "export_report"] * 2, start_day=2)
    (f,) = await detect_mechanical_chains("inst_chain", days=7, threshold=2)
    assert f["days_seen"] == 2


@pytest.mark.asyncio
async def test_a_denied_chain_is_not_reproposed_in_any_rotation(brain):
    from lhassa.agents.repetition import detect_mechanical_chains

    await _seq(brain, ["web_fetch", "export_report"] * 4)
    ap = await brain.add_approval(
        "inst_chain", "mint_script",
        {"accion": "mint_script", "pattern": "cron::chain:export_report→web_fetch"},
    )
    await brain.resolve_approval("inst_chain", ap, "rechazada")  # a HUMAN denial
    assert await detect_mechanical_chains("inst_chain", days=7, threshold=2) == []


@pytest.mark.asyncio
async def test_queue_closures_do_not_count_as_denials_but_human_denials_do(brain):
    from lhassa.agents.repetition import denied_patterns

    expired = await brain.add_approval("i", "mint_script", {"pattern": "p::expired"})
    await brain.close_approval("i", expired, "rejected-by-timeout")
    capped = await brain.add_approval("i", "mint_memory", {"pattern": "p::capped"})
    await brain.close_approval("i", capped, "rejected-by-cap")
    human = await brain.add_approval("i", "mint_script", {"pattern": "p::human"})
    await brain.resolve_approval("i", human, "rechazada")
    assert await denied_patterns("i") == {"p::human"}


@pytest.mark.asyncio
async def test_selfimprove_does_not_repropose_a_rotation_already_seen(brain, monkeypatch):
    """An old, non-canonical seen key must also suppress the canonical rotation."""
    from unittest.mock import AsyncMock, patch

    from lhassa.actionpack._selfimprove import detect_repetition as sweep

    await _seq(brain, ["web_fetch", "export_report"] * 4, instance_id="inst_chain")
    await brain.set_watch_state("inst_chain", "repetition:seen:cron::chain:web_fetch→export_report", "1")
    with patch("lhassa.actionpack._selfimprove._notify", AsyncMock(return_value=True)):
        await sweep({"instance_id": "inst_chain", "days": 7})
    pending = await brain.load_pending_approvals("inst_chain")
    assert [a for a in pending if a["tipo"] == "mint_script"] == []


@pytest.mark.asyncio
async def test_selfimprove_mint_script_payload_carries_days_and_respects_the_cap(brain, monkeypatch):
    from unittest.mock import AsyncMock, patch

    from lhassa.actionpack import _selfimprove

    monkeypatch.setattr(_selfimprove.Config, "approval_pending_cap", 1, raising=False)
    await _seq(brain, ["web_fetch", "export_report"] * 4, instance_id="inst_chain")
    await _seq(brain, ["ping_url", "list_dir"] * 4, despertar_por="other", instance_id="inst_chain")
    with patch("lhassa.actionpack._selfimprove._notify", AsyncMock(return_value=True)):
        await _selfimprove.detect_repetition({"instance_id": "inst_chain", "days": 7})
    rows = [a for a in await brain.load_pending_approvals("inst_chain") if a["tipo"] == "mint_script"]
    assert len(rows) == 1  # the pending cap (1) held the second back
    assert rows[0]["payload"]["days_seen"] >= 1
