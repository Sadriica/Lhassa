"""Tests for lhassa.core.policy — the deterministic "reflexes" triage layer.

Fully offline: no DB, no LLM, no network. Each reflex is a pure function of a
Trigger; ``evaluate`` is tested directly against synthetic triggers.
"""

from lhassa.config import Config
from lhassa.core import policy
from lhassa.core.watchlist import Trigger


# ── job_review reflex ────────────────────────────────────────────────────────


def test_job_review_clean_success_skips():
    trigger = Trigger(
        kind="job",
        action="job_review",
        objetivo="j_1",
        params={"job_id": "j_1", "estado": "done", "summary": "job j_1 via opencode: done ($0.0000)"},
    )
    decision = policy.evaluate(trigger)
    assert decision.kind == "skip"
    assert decision.hard is False
    assert "clean" in decision.reason


def test_job_review_failed_job_needs_judgment():
    trigger = Trigger(
        kind="job",
        action="job_review",
        objetivo="j_2",
        params={"job_id": "j_2", "estado": "error", "summary": "job j_2 via opencode: error ($0.0000)"},
    )
    decision = policy.evaluate(trigger)
    assert decision.kind == "think"
    assert decision.hard is True
    assert decision.context_tier == "full"


def test_job_review_expensive_job_needs_judgment():
    trigger = Trigger(
        kind="job",
        action="job_review",
        objetivo="j_3",
        params={"job_id": "j_3", "estado": "done", "summary": "job j_3 via claude: done ($1.2500)"},
    )
    decision = policy.evaluate(trigger)
    assert decision.kind == "think"
    assert decision.hard is True


def test_job_review_unparseable_cost_defaults_to_judgment():
    # No dollar amount in the summary at all → conservative: not "clean".
    trigger = Trigger(
        kind="job", action="job_review", objetivo="j_4",
        params={"job_id": "j_4", "estado": "done", "summary": "job j_4 via opencode: done"},
    )
    decision = policy.evaluate(trigger)
    assert decision.kind == "think"


# ── repo_changed reflex ──────────────────────────────────────────────────────


def test_repo_changed_on_auto_sync_allowlist_runs_action(monkeypatch):
    monkeypatch.setattr(Config, "auto_sync_repos", "/repos/a,/repos/b", raising=False)
    trigger = Trigger(
        kind="repo", action="repo_changed", objetivo="/repos/a",
        params={"old": "aaa", "new": "bbb"},
    )
    decision = policy.evaluate(trigger)
    assert decision.kind == "action"
    assert decision.action == "sync_repo"
    assert decision.params == {"repo_dir": "/repos/a"}


def test_repo_changed_not_on_allowlist_thinks(monkeypatch):
    monkeypatch.setattr(Config, "auto_sync_repos", "/repos/a", raising=False)
    trigger = Trigger(
        kind="repo", action="repo_changed", objetivo="/repos/other",
        params={"old": "aaa", "new": "bbb"},
    )
    decision = policy.evaluate(trigger)
    assert decision.kind == "think"
    assert decision.hard is False
    assert decision.context_tier == "minimal"


def test_repo_changed_empty_allowlist_thinks(monkeypatch):
    monkeypatch.setattr(Config, "auto_sync_repos", "", raising=False)
    trigger = Trigger(kind="repo", action="repo_changed", objetivo="/repos/a")
    decision = policy.evaluate(trigger)
    assert decision.kind == "think"


# ── health_alert reflex ──────────────────────────────────────────────────────


def test_health_alert_warn_notifies_directly():
    trigger = Trigger(
        kind="health", action="health_alert",
        params={"level": "warn", "summary": "disk: 92% used"},
    )
    decision = policy.evaluate(trigger)
    assert decision.kind == "action"
    assert decision.action == "notify"
    assert decision.params["level"] == "warning"
    assert "disk" in decision.params["text"]


def test_health_alert_alert_level_needs_judgment():
    trigger = Trigger(
        kind="health", action="health_alert",
        params={"level": "alert", "summary": "disk: 99% used"},
    )
    decision = policy.evaluate(trigger)
    assert decision.kind == "think"
    assert decision.hard is True
    assert decision.context_tier == "full"


# ── default reflex / fallback ────────────────────────────────────────────────


def test_unmatched_trigger_falls_to_default_reflex():
    trigger = Trigger(kind="task", action="tracking_postulaciones", objetivo="")
    decision = policy.evaluate(trigger)
    assert decision.kind == "think"
    assert decision.hard is False
    assert decision.context_tier == "minimal"


def test_evaluate_never_raises_on_a_broken_reflex(monkeypatch):
    """A reflex that raises must be skipped, not crash evaluate()."""

    def _boom(trigger):
        raise RuntimeError("reflex bug")

    # Insert a broken reflex ahead of "default" so it is tried and must be
    # swallowed; "default" still resolves the decision afterwards.
    policy.register_reflex("broken_test_reflex", _boom, override=True)
    try:
        trigger = Trigger(kind="anything", action="whatever")
        decision = policy.evaluate(trigger)
        assert decision.kind == "think"  # degraded safely via "default"
    finally:
        policy.REFLEXES.unregister("broken_test_reflex")


def test_evaluate_degrades_safely_when_reflex_iteration_itself_errors(monkeypatch):
    """If REFLEXES.names() itself blows up, evaluate() still returns a safe decision."""

    def _boom_names():
        raise RuntimeError("registry bug")

    monkeypatch.setattr(policy.REFLEXES, "names", _boom_names)
    decision = policy.evaluate(Trigger(kind="x", action="y"))
    assert decision.kind == "think"
    assert decision.context_tier == "minimal"
    assert decision.hard is False


# ── registry ordering ────────────────────────────────────────────────────────


def test_reflexes_registered_in_expected_order():
    # First-non-None-wins depends on registration order; "default" must be last
    # so every specific reflex gets a chance to match first.
    assert policy.REFLEXES.names()[-1] == "default"
    assert "job_review" in policy.REFLEXES.names()
    assert "repo_changed" in policy.REFLEXES.names()
    assert "health_alert" in policy.REFLEXES.names()
