"""
Tests for ``lhassa.agents.verify``: the objective gates that NEVER call an LLM.

Fully offline: the ``_run`` subprocess seam and ``health.run_all`` are mocked, so
nothing is spawned and no network/LLM is touched. A dedicated test patches the
LLM provider to blow up and confirms the verifiers still run; proving the gate
is model-free.
"""

import subprocess

import pytest

from lhassa.agents import verify as V


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


# ── VerifyResult ─────────────────────────────────────────────────────────────


def test_verify_result_is_truthy_on_pass():
    assert bool(V.VerifyResult(True)) is True
    assert bool(V.VerifyResult(False)) is False
    r = V.VerifyResult(True, "ok", 1.5)
    assert (r.passed, r.detail, r.metric) == (True, "ok", 1.5)


def test_verifier_protocol_is_satisfied_by_builtins():
    for v in (V.CommandVerifier("x"), V.RegexVerifier("a"), V.HealthVerifier()):
        assert isinstance(v, V.Verifier)


# ── CommandVerifier / Tests / Lint (exit-code gates) ─────────────────────────


def test_command_verifier_pass_on_exit_zero(monkeypatch):
    v = V.CommandVerifier("true")
    monkeypatch.setattr(v, "_run", lambda cmd: _proc(0, "all good"))
    r = v.verify()
    assert r.passed is True
    assert "exit=0" in r.detail and "all good" in r.detail


def test_command_verifier_fail_on_nonzero_with_detail(monkeypatch):
    v = V.CommandVerifier("false")
    monkeypatch.setattr(v, "_run", lambda cmd: _proc(2, "", "boom"))
    r = v.verify()
    assert r.passed is False
    assert "exit=2" in r.detail and "boom" in r.detail


def test_command_verifier_subprocess_error_is_failed(monkeypatch):
    v = V.CommandVerifier("nope")

    def _raise(cmd):
        raise OSError("no such binary")

    monkeypatch.setattr(v, "_run", _raise)
    r = v.verify()
    assert r.passed is False
    assert "command error" in r.detail


def test_command_verifier_truncates_long_output(monkeypatch):
    v = V.CommandVerifier("x")
    monkeypatch.setattr(v, "_run", lambda cmd: _proc(0, "z" * 5000))
    r = v.verify()
    assert "+" in r.detail and "chars" in r.detail
    assert len(r.detail) < 5000


def test_tests_and_lint_verifiers_default_names_and_pass(monkeypatch):
    for cls, name in ((V.TestsVerifier, "tests"), (V.LintVerifier, "lint")):
        v = cls()
        assert v.name == name
        monkeypatch.setattr(v, "_run", lambda cmd: _proc(0))
        assert v.verify().passed is True
        monkeypatch.setattr(v, "_run", lambda cmd: _proc(1, "fail"))
        assert v.verify().passed is False


# ── HealthVerifier (digest → pass/fail) ──────────────────────────────────────


def test_health_verifier_pass_when_ok(monkeypatch):
    v = V.HealthVerifier()
    monkeypatch.setattr(v, "_run", lambda cmd=None: {"ok": True, "worst_level": "info"})
    r = v.verify()
    assert r.passed is True
    assert "worst_level=info" in r.detail


def test_health_verifier_fail_on_warn_or_alert(monkeypatch):
    for worst in ("warn", "alert"):
        v = V.HealthVerifier()
        monkeypatch.setattr(
            v, "_run", lambda cmd=None, w=worst: {"ok": False, "worst_level": w}
        )
        assert v.verify().passed is False


def test_health_verifier_maps_digest_via_run_all(monkeypatch):
    # Patch the real seam target: health.checks.run_all.
    from lhassa.health import checks

    called = {}

    def fake_run_all(out_dir=None, notify=True):
        called["notify"] = notify
        return {"ok": True, "worst_level": "notice"}

    monkeypatch.setattr(checks, "run_all", fake_run_all)
    r = V.HealthVerifier().verify()
    assert r.passed is True
    assert called["notify"] is False  # verifier never fires notifications


def test_health_verifier_error_is_failed(monkeypatch):
    v = V.HealthVerifier()

    def _boom(cmd=None):
        raise RuntimeError("collector down")

    monkeypatch.setattr(v, "_run", _boom)
    r = v.verify()
    assert r.passed is False and "error" in r.detail


# ── RegexVerifier (match / no-match) ─────────────────────────────────────────


def test_regex_must_match():
    v = V.RegexVerifier(r"OK\b")
    assert v.verify("status: OK now").passed is True
    assert v.verify("status: bad").passed is False


def test_regex_must_not_match():
    v = V.RegexVerifier(r"ERROR", must_match=False)
    assert v.verify("all fine").passed is True
    assert v.verify("got ERROR here").passed is False


def test_regex_coerces_non_string_artifact():
    v = V.RegexVerifier(r"42")
    assert v.verify(42).passed is True


# ── MetricVerifier (beats a baseline) ────────────────────────────────────────


def test_metric_higher_is_better():
    assert V.MetricVerifier(0.9, 0.8).verify().passed is True
    assert V.MetricVerifier(0.7, 0.8).verify().passed is False
    assert V.MetricVerifier(0.8, 0.8).verify().passed is False  # must beat, not tie


def test_metric_lower_is_better():
    v = V.MetricVerifier(120.0, 200.0, higher_is_better=False)
    r = v.verify()
    assert r.passed is True and r.metric == 120.0


def test_metric_callable_measures_artifact():
    v = V.MetricVerifier(lambda art, ctx: len(art), 3)
    assert v.verify("hello").passed is True   # len 5 > 3
    assert v.verify("ab").passed is False     # len 2 < 3


def test_metric_callable_single_arg_fallback():
    v = V.MetricVerifier(lambda art: float(art), 10)
    assert v.verify(11).passed is True


def test_metric_error_is_failed():
    v = V.MetricVerifier(lambda art, ctx: 1 / 0, 1)
    assert v.verify("x").passed is False


# ── ResearchCorpusVerifier (shape/citations/experiment, no LLM) ─────────────

def _valid_corpus():
    return """## The idea in one sentence
One bounded claim [S1].

## Mechanics - down to the base
The captured mechanism has two stages [S1].

## Evidence the authors present
- The authors report a result [S1].
- A second claim is UNVERIFIED.

## Sources
- S1: https://example.test/paper; sha256=abc

## Mapping to Lhassa
No mapping has been verified.

## Not yet verified by us - and the experiment that would
Baseline: current route
Instrument: LhassaBench
Metric: verified pass rate
Stop rule: 20 runs or the budget cap

## Open questions
- Does it generalize?
"""


def test_research_corpus_verifier_passes_complete_cited_draft():
    result = V.verify(_valid_corpus(), spec={"type": "research_corpus"})
    assert result.passed is True
    assert "7 sections" in result.detail


def test_research_corpus_verifier_rejects_unsupported_evidence_bullet():
    text = _valid_corpus().replace(
        "- The authors report a result [S1].",
        "- The authors report a result.",
    )
    result = V.ResearchCorpusVerifier().verify(text)
    assert result.passed is False
    assert "lacks [S#] or UNVERIFIED" in result.detail


def test_research_corpus_verifier_rejects_symbolic_last_heading_gate():
    result = V.ResearchCorpusVerifier().verify("## Open questions\nAnything")
    assert result.passed is False
    assert "The idea in one sentence" in result.detail


def test_idea_assessment_verifier_recomputes_the_budget():
    import json

    from tests.test_idea_assessments import document

    result = V.IdeaAssessmentVerifier().verify(json.dumps(document(3)))
    assert result.passed is True
    assert "rigorous → experiment" in result.detail


# ── dispatcher: make_verifier / verify ───────────────────────────────────────


def test_make_verifier_builds_each_type():
    assert isinstance(V.make_verifier({"type": "command", "cmd": "x"}), V.CommandVerifier)
    assert isinstance(V.make_verifier({"type": "regex", "pattern": "a"}), V.RegexVerifier)
    assert isinstance(V.make_verifier({"type": "health"}), V.HealthVerifier)


def test_make_verifier_unknown_type_raises():
    with pytest.raises(ValueError):
        V.make_verifier({"type": "teleport"})


def test_make_verifier_bad_args_raises_valueerror():
    with pytest.raises(ValueError):
        V.make_verifier({"type": "regex", "bogus": 1})


def test_top_level_verify_runs_regex():
    r = V.verify("hello OK", spec={"type": "regex", "pattern": "OK"})
    assert r.passed is True


def test_top_level_verify_degrades_on_bad_spec():
    # Never raises; a bad spec is a failed result.
    assert V.verify("x", spec={"type": "nope"}).passed is False
    assert V.verify("x", spec={}).passed is False


# ── keystone registry: introspection + drop-in seam ──────────────────────────


def test_verifiers_registry_exposes_builtin_names():
    # The capabilities slice introspects ``VERIFIERS`` (a keystone Registry).
    from lhassa.registry import Registry

    assert isinstance(V.VERIFIERS, Registry)
    assert set(V.VERIFIERS.names()) == {
        "command", "tests", "lint", "health", "regex", "metric",
        "research_corpus", "idea_assessment", "contact_preserved",
    }
    # The legacy ``_REGISTRY`` alias is a live read-only view of the same store.
    assert V._REGISTRY.get("regex") is V.RegexVerifier


def test_register_verifier_dropin_and_make(monkeypatch):
    """A plugin can register a new verifier type, then make_verifier builds it."""

    class AlwaysVerifier:
        name = "always"

        def __init__(self, *, value=True):
            self.value = value

        def verify(self, artifact=None, context=None):
            return V.VerifyResult(self.value, "always")

    try:
        V.register_verifier("always", AlwaysVerifier)
        built = V.make_verifier({"type": "always", "value": True})
        assert isinstance(built, AlwaysVerifier)
        assert built.verify().passed is True
        # The top-level helper resolves the new type too.
        assert V.verify("x", spec={"type": "always", "value": False}).passed is False
    finally:
        V.VERIFIERS.unregister("always")


def test_register_verifier_override_protection():
    with pytest.raises(ValueError, match="already registered"):
        V.register_verifier("regex", V.RegexVerifier)
    # override=True replaces without raising; restore afterwards.
    try:
        V.register_verifier("regex", V.CommandVerifier, override=True)
        assert V.VERIFIERS.get("regex") is V.CommandVerifier
    finally:
        V.register_verifier("regex", V.RegexVerifier, override=True)


# ── the model-free invariant ─────────────────────────────────────────────────


def test_verifiers_never_invoke_an_llm(monkeypatch):
    """Patch the provider factory + gateway to explode; verifiers must still run."""
    import lhassa.llm.provider as provider
    import lhassa.llm.gateway as gateway

    def _explode(*a, **k):
        raise AssertionError("a verifier called into the LLM stack")

    monkeypatch.setattr(provider, "get_model_provider", _explode, raising=False)
    monkeypatch.setattr(gateway, "complete", _explode, raising=False)

    # Pure gates run with no provider in sight.
    assert V.RegexVerifier("OK").verify("OK").passed is True
    assert V.MetricVerifier(2.0, 1.0).verify().passed is True

    cmd = V.CommandVerifier("x")
    monkeypatch.setattr(cmd, "_run", lambda c: _proc(0, "fine"))
    assert cmd.verify().passed is True

    health = V.HealthVerifier()
    monkeypatch.setattr(health, "_run", lambda c=None: {"ok": True, "worst_level": "info"})
    assert health.verify().passed is True
