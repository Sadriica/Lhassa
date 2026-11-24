"""Task suite (classify, summarize, route, policy, extract); fully offline.

Every fixture ships a ``reference`` answer that MUST pass its own verifier,
and, where the case is tricky, ``bad`` answers that MUST fail. Label tasks are
also checked exhaustively: every other label in the closed set must fail.
None of this needs a model; real measurements live in
``scripts/bench_small_models.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from lhassa.agents import verify as verify_agent
from lhassa.bench import loader, runner, verifiers

TASKS = ("classify", "summarize", "route", "policy", "extract")
FIXTURES = loader.load_task_fixtures()


def _score(fixture: dict, text: str):
    return verify_agent.verify(text, spec=loader.resolve_verify_spec(fixture["verify"]))


def test_each_task_has_at_least_ten_cases_and_ids_are_unique():
    for task in TASKS:
        assert len(loader.load_task_fixtures(task)) >= 10, task
    ids = [f["id"] for f in FIXTURES]
    assert len(ids) == len(set(ids))
    assert {f["task"] for f in FIXTURES} == set(TASKS)


def test_task_suite_is_not_in_the_default_fixture_set():
    default_ids = {f["id"] for f in loader.load_fixtures()}
    assert not default_ids & {f["id"] for f in FIXTURES}


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["id"])
def test_reference_passes_and_bad_answers_fail(fixture):
    assert _score(fixture, fixture["reference"]).passed, _score(fixture, fixture["reference"]).detail
    for bad in fixture.get("bad", []):
        assert not _score(fixture, bad).passed
    assert not _score(fixture, "I am not sure.").passed
    assert not _score(fixture, "").passed
    assert fixture.get("max_tokens", 0) >= 32


@pytest.mark.parametrize(
    "fixture", [f for f in FIXTURES if f["verify"]["type"] == "label"], ids=lambda f: f["id"]
)
def test_every_other_label_fails(fixture):
    for label in fixture["verify"]["labels"]:
        res = _score(fixture, label)
        assert res.passed == (verifiers.normalize_label(label) == verifiers.normalize_label(fixture["reference"]))


def test_hard_cases_exist_in_every_task():
    for task in TASKS:
        assert any("hard" in f.get("tags", []) for f in FIXTURES if f["task"] == task), task


def test_run_bench_task_suite_scores_references(monkeypatch):
    by_prompt = {f["prompt"]: f for f in FIXTURES}

    class _Cfg:
        instance_id = "inst_test"

    class _Comp:
        def __init__(self, text):
            self.text, self.cost_usd = text, 0.0

    async def fake(provider, prompt, *, max_tokens):
        return _Comp(by_prompt[prompt]["reference"])

    monkeypatch.setattr("lhassa.bench.runner._resolve_subject", lambda s, c: object())
    rows = asyncio.run(
        runner.run_bench(["fake"], config=_Cfg(), complete_fn=fake, persist=False, suite="tasks")
    )
    assert len(rows) == len(FIXTURES)
    assert all(r["passed"] for r in rows), [r["detail"] for r in rows if not r["passed"]]


# ── verifier units ───────────────────────────────────────────────────────────

def test_label_tolerates_formatting_but_not_extra_words():
    v = verifiers.LabelVerifier("DENY:P1")
    assert v.verify("  `deny:p1`.\n").passed
    assert v.verify("<think>hmm ALLOW</think>\nDENY:P1").passed
    assert not v.verify("DENY:P1 because the path is protected").passed
    assert not v.verify("ALLOW").passed


def test_facts_alternatives_regex_forbidden_and_budget():
    v = verifiers.FactsVerifier(["r-41", "rolled back|reverted", "re:HTTP\\s*5\\d\\d"],
                                forbidden=["succeeded"], max_words=8)
    assert v.verify("r-41 was reverted after HTTP 503").passed
    assert not v.verify("r-41 succeeded, HTTP 503, reverted").passed
    assert not v.verify("r-41 reverted").passed
    long = "r-41 reverted HTTP 503 " + "word " * 10
    assert "words=" in v.verify(long).detail


def test_json_fields_fences_prose_and_item_level_groups():
    v = verifiers.JsonFieldsVerifier(
        required_paths=["todos"],
        lists={"todos": {"min": 1, "max": 1, "groups": [["ana", "report"]]}},
    )
    ok = '{"todos": [{"owner": "Ana", "task": "Write the report", "due": null}]}'
    assert v.verify(ok).passed
    assert v.verify("Sure!\n```json\n" + ok + "\n```").passed
    split = '{"todos": [{"owner": "Ana", "task": "x"}]}'
    assert not v.verify(split).passed  # owner and keyword must share one item
    assert not v.verify("not json at all").passed
    assert v.verify("not json at all").metric == 0.0


def test_strip_reasoning_handles_unterminated_block():
    assert verifiers.strip_reasoning("<think>abc") == ""
    assert verifiers.strip_reasoning("<think>a</think> hi") == "hi"
