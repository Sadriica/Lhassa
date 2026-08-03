"""LhassaBench (Wave E) — fully offline.

Covers: every bundled fixture loads and its ``verify`` spec constructs
cleanly through the REAL :func:`lhassa.agents.verify.make_verifier` (no
bypass); :func:`lhassa.bench.runner.run_bench` scores pass/fail correctly
with a stubbed ``complete_fn`` and writes rows to ``bench_runs``; an
unresolvable subject degrades to an honest failed row, never an exception;
:func:`lhassa.bench.runner.summarize` reports pass-rates; and the scorer is
LOCKED to ``agents.verify.verify`` — monkeypatching it to raise still yields
failed rows through ``run_bench``, proving there is no separate/bypassable
scoring path.
"""

from __future__ import annotations

import asyncio

import pytest

from lhassa.agents import verify as verify_agent
from lhassa.bench import loader, runner


# ── DB fixture (mirrors tests/test_projects.py's pattern) ────────────────────

@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.security import signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    asyncio.run(db.init_db())
    return db


class FakeConfig:
    """Minimal Config stand-in: instance_id + the CLI-binary attrs the
    subject-availability check reads."""

    def __init__(self, instance_id="inst_test"):
        self.instance_id = instance_id


class FakeCompletion:
    def __init__(self, text, cost_usd=0.0):
        self.text = text
        self.cost_usd = cost_usd


class FakeProvider:
    name = "fake"
    zero_retention = False
    is_local = False

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        return FakeCompletion("unused — complete_fn stub bypasses this")


# ── fixtures: load + validate against the real verifier ──────────────────────

def test_all_fixtures_load():
    fixtures = loader.load_fixtures()
    assert len(fixtures) >= 10
    ids = [f["id"] for f in fixtures]
    assert len(ids) == len(set(ids)), "fixture ids must be unique"


def test_every_fixture_verify_spec_builds_a_real_verifier():
    for fixture in loader.load_fixtures():
        spec = loader.resolve_verify_spec(fixture["verify"])
        verifier = verify_agent.make_verifier(spec)
        assert verifier.name


def test_fixture_tag_mix_covers_all_four_categories():
    fixtures = loader.load_fixtures()
    all_tags = {t for f in fixtures for t in f.get("tags", [])}
    assert {"codegen", "factual", "instruction-following", "extraction"} <= all_tags


def test_bad_metric_fn_name_raises_fixture_error():
    with pytest.raises(loader.FixtureError):
        loader.resolve_verify_spec({"type": "metric", "metric_fn": "not_a_real_metric", "baseline": 1})


# ── run_bench: stubbed complete_fn, offline ───────────────────────────────────

_CANNED_ANSWERS = {
    "codegen_fibonacci": "def fibonacci(n):\n    return n",
    "codegen_is_palindrome": "def is_palindrome(s):\n    return s == s[::-1]",
    "codegen_add_numbers": "def add_numbers(a, b):\n    return a + b",
    "factual_capital_france": "Paris",
    "factual_yes_no": "Yes",
    "factual_three_bullets": "- one\n- two\n- three",
    "instruction_marker_wrap": "[[START]] lighthouse [[END]]",
    "instruction_brief_answer": "A stack is last-in first-out storage.",
    "extraction_email": "maria.gomez@example.com",
    "extraction_apple_count": "7",
}

# Deliberately wrong for every fixture, INCLUDING the metric one (16 words,
# over the instruction_brief_answer 15-word budget) so every regex AND the
# one metric fixture fail together.
_WRONG_ANSWER = (
    "this reply intentionally matches absolutely nothing that any single one "
    "of the ten bundled fixtures is looking for at all"
)


def _fixture_id_for_prompt(prompt: str) -> str:
    for fixture in loader.load_fixtures():
        if fixture["prompt"] == prompt:
            return fixture["id"]
    raise AssertionError(f"no fixture with prompt {prompt!r}")


async def _canned_complete_fn(provider, prompt, *, max_tokens):
    fixture_id = _fixture_id_for_prompt(prompt)
    text = _CANNED_ANSWERS.get(fixture_id, _WRONG_ANSWER)
    return FakeCompletion(text, cost_usd=0.001)


async def _wrong_complete_fn(provider, prompt, *, max_tokens):
    return FakeCompletion(_WRONG_ANSWER, cost_usd=0.0)


def _run(subjects, **kw):
    async def _go():
        return await runner.run_bench(
            subjects, config=FakeConfig(), complete_fn=kw.pop("complete_fn"), **kw
        )

    return asyncio.run(_go())


def test_run_bench_scores_correct_answers_as_passed(brain, monkeypatch):
    monkeypatch.setattr(
        "lhassa.bench.runner._resolve_subject", lambda subject, config: FakeProvider()
    )
    rows = _run(["fake"], complete_fn=_canned_complete_fn)
    assert len(rows) == len(loader.load_fixtures())
    assert all(r["passed"] for r in rows), [r["detail"] for r in rows if not r["passed"]]
    assert all(r["subject"] == "fake" for r in rows)
    assert all(r["cost_usd"] == pytest.approx(0.001) for r in rows)


def test_run_bench_scores_wrong_answers_as_failed(brain, monkeypatch):
    monkeypatch.setattr(
        "lhassa.bench.runner._resolve_subject", lambda subject, config: FakeProvider()
    )
    rows = _run(["fake"], complete_fn=_wrong_complete_fn)
    assert len(rows) == len(loader.load_fixtures())
    assert not any(r["passed"] for r in rows)


def test_run_bench_persists_rows_to_bench_runs(brain, monkeypatch):
    monkeypatch.setattr(
        "lhassa.bench.runner._resolve_subject", lambda subject, config: FakeProvider()
    )
    rows = _run(["fake"], fixtures=["factual_capital_france"], complete_fn=_canned_complete_fn)
    assert len(rows) == 1

    stored = asyncio.run(brain.list_bench_runs("inst_test"))
    assert len(stored) == 1
    assert stored[0]["fixture_id"] == "factual_capital_france"
    assert stored[0]["subject"] == "fake"
    assert bool(stored[0]["passed"]) is True


def test_run_bench_restricts_to_requested_fixtures(brain, monkeypatch):
    monkeypatch.setattr(
        "lhassa.bench.runner._resolve_subject", lambda subject, config: FakeProvider()
    )
    rows = _run(
        ["fake"],
        fixtures=["factual_capital_france", "extraction_apple_count"],
        complete_fn=_canned_complete_fn,
    )
    assert {r["fixture_id"] for r in rows} == {"factual_capital_france", "extraction_apple_count"}


def test_unresolvable_subject_yields_honest_failed_row_not_exception(brain, monkeypatch):
    monkeypatch.setattr(
        "lhassa.bench.runner._resolve_subject", lambda subject, config: None
    )
    rows = _run(["nonexistent-subject"], fixtures=["factual_capital_france"], complete_fn=_canned_complete_fn)
    assert len(rows) == 1
    assert rows[0]["passed"] is False
    assert "could not resolve" in rows[0]["detail"]


def test_cli_subject_with_missing_binary_yields_honest_failed_row(brain, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda binary: None)
    rows = _run(["claude"], fixtures=["factual_capital_france"], complete_fn=_canned_complete_fn)
    assert len(rows) == 1
    assert rows[0]["passed"] is False
    assert "not installed" in rows[0]["detail"]


def test_provider_error_yields_failed_row_not_exception(brain, monkeypatch):
    monkeypatch.setattr(
        "lhassa.bench.runner._resolve_subject", lambda subject, config: FakeProvider()
    )

    async def _boom(provider, prompt, *, max_tokens):
        raise RuntimeError("network is down")

    rows = _run(["fake"], fixtures=["factual_capital_france"], complete_fn=_boom)
    assert len(rows) == 1
    assert rows[0]["passed"] is False
    assert "network is down" in rows[0]["detail"]


# ── the scorer is LOCKED to agents.verify.verify (no bypass) ─────────────────

def test_scorer_is_locked_to_agents_verify(brain, monkeypatch):
    monkeypatch.setattr(
        "lhassa.bench.runner._resolve_subject", lambda subject, config: FakeProvider()
    )

    def _raise(*a, **kw):
        raise RuntimeError("verifier exploded")

    monkeypatch.setattr(verify_agent, "verify", _raise)
    rows = _run(["fake"], fixtures=["factual_capital_france"], complete_fn=_canned_complete_fn)
    assert len(rows) == 1
    # The raise propagates out of run_bench's own call to verify_agent.verify —
    # proving run_bench really calls THAT function (not a private copy) — but
    # run_bench still must not crash: it degrades to a failed row.
    assert rows[0]["passed"] is False


# ── summarize ─────────────────────────────────────────────────────────────────

def test_summarize_reports_pass_rates():
    rows = [
        {"subject": "a", "passed": True, "metric": 1.0, "cost_usd": 0.01},
        {"subject": "a", "passed": False, "metric": None, "cost_usd": 0.02},
        {"subject": "b", "passed": True, "metric": None, "cost_usd": 0.0},
    ]
    text = runner.summarize(rows)
    assert "a" in text and "b" in text
    assert "50.0%" in text  # subject a: 1/2 passed
    assert "100.0%" in text  # subject b: 1/1 passed


def test_summarize_empty():
    assert "no rows" in runner.summarize([]).lower()
