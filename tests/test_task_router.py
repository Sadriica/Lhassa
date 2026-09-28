"""Tests for the versatile-brain composition seam (lhassa/notify/task_router.py,
Plan 4 Stream 1).

The DoD for this stream: the SAME request at minimal vs. deep effort produces
a visibly different trajectory (mode/budget/turns/verifiers/moa), and the
whole thing is resolvable (cost declared) before any spend.
"""

from lhassa.notify.task_router import describe, route


def test_route_resolves_all_three_axes():
    decision = route("build a script to parse logs")
    assert decision.work_type == "build"
    assert decision.mode.name == "iterate_with_verifier"
    assert decision.effort.level in {"standard"}  # build's policy default


def test_same_request_minimal_vs_deep_diverges_visibly():
    minimal = route("investigate the new API --minimal")
    deep = route("investigate the new API --deep")

    assert minimal.effort.level == "minimal"
    assert deep.effort.level == "deep"
    # Work type and mode are identical (same request); only effort differs...
    assert minimal.work_type == deep.work_type == "research"
    assert minimal.mode.name == deep.mode.name == "search_and_cite"
    # ...but the trajectory diverges on every governed axis.
    assert minimal.effort.token_budget < deep.effort.token_budget
    assert minimal.effort.loop_turns < deep.effort.loop_turns
    assert minimal.effort.verifier_count < deep.effort.verifier_count
    assert minimal.effort.moa is False
    assert deep.effort.moa is True
    assert minimal.effort.estimated_cost_usd < deep.effort.estimated_cost_usd


def test_marker_stripped_from_routed_text():
    decision = route("summarize this document --exhaustive")
    assert "--exhaustive" not in decision.text
    assert "summarize this document" in decision.text


def test_describe_declares_cost_and_trajectory_before_spending():
    minimal = describe(route("what is TCP --minimal"))
    deep = describe(route("what is TCP --deep"))
    assert "effort=minimal" in minimal
    assert "effort=deep" in deep
    assert "declared_cost" in minimal
    assert "declared_cost" in deep
    # The line itself proves the two trajectories are not the same string.
    assert minimal != deep


def test_route_never_raises_on_bad_input():
    decision = route("")
    assert decision.work_type == "question"
    assert decision.mode.name == "answer_directly"

    decision_none = route(None)  # type: ignore[arg-type]
    assert decision_none.work_type == "question"


def test_route_effort_override_short_circuits_everything():
    decision = route("build something", effort_override="exhaustive")
    assert decision.effort.level == "exhaustive"
