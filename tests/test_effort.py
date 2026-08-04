"""Tests for the effort-level axis (lhassa/core/effort.py, Plan 4 Stream 1).

Effort must be DECLARED before anything is spent, never discovered by
spending — these tests check the resolver's priority order, that levels are
monotonically bigger, and that a marker never leaks into the cleaned text.
"""

from lhassa.core.effort import (
    DEFAULT_EFFORT_BY_TYPE,
    EFFORT_LEVELS,
    GLOBAL_DEFAULT_EFFORT,
    parse_effort_marker,
    resolve_effort,
)


def test_effort_levels_are_the_documented_four():
    assert EFFORT_LEVELS == ("minimal", "standard", "deep", "exhaustive")


def test_profiles_are_monotonically_bigger():
    profiles = [resolve_effort(override=level)[0] for level in EFFORT_LEVELS]
    tokens = [p.token_budget for p in profiles]
    turns = [p.loop_turns for p in profiles]
    verifiers = [p.verifier_count for p in profiles]
    costs = [p.estimated_cost_usd for p in profiles]
    assert tokens == sorted(tokens)
    assert turns == sorted(turns)
    assert verifiers == sorted(verifiers)
    assert costs == sorted(costs)
    # Strictly bigger, not just non-decreasing — the whole point of the axis.
    assert len(set(tokens)) == len(tokens)


def test_moa_only_opens_at_deep_and_exhaustive():
    assert resolve_effort(override="minimal")[0].moa is False
    assert resolve_effort(override="standard")[0].moa is False
    assert resolve_effort(override="deep")[0].moa is True
    assert resolve_effort(override="exhaustive")[0].moa is True


def test_parse_effort_marker_extracts_and_strips():
    level, cleaned = parse_effort_marker("investigate embeddings --deep please")
    assert level == "deep"
    assert "--deep" not in cleaned
    assert "investigate embeddings" in cleaned
    assert "please" in cleaned


def test_parse_effort_marker_absent():
    level, cleaned = parse_effort_marker("just a normal question")
    assert level is None
    assert cleaned == "just a normal question"


def test_parse_effort_marker_never_raises_on_bad_input():
    assert parse_effort_marker(None) == (None, "")  # type: ignore[arg-type]
    assert parse_effort_marker("") == (None, "")


def test_resolve_effort_priority_override_wins_over_marker():
    profile, cleaned = resolve_effort(
        work_type="question", message="hi --exhaustive", override="minimal"
    )
    assert profile.level == "minimal"
    # The message-level marker is still stripped even when the override wins.
    assert "--exhaustive" not in cleaned


def test_resolve_effort_priority_marker_wins_over_work_type_default():
    profile, cleaned = resolve_effort(work_type="question", message="explain this --deep")
    assert profile.level == "deep"
    assert "--deep" not in cleaned


def test_resolve_effort_priority_work_type_default_wins_over_global():
    for work_type, expected_level in DEFAULT_EFFORT_BY_TYPE.items():
        profile, _ = resolve_effort(work_type=work_type, message="a plain message")
        assert profile.level == expected_level


def test_resolve_effort_global_default_when_nothing_else_given():
    profile, _ = resolve_effort()
    assert profile.level == GLOBAL_DEFAULT_EFFORT


def test_resolve_effort_bad_override_degrades_instead_of_raising():
    profile, _ = resolve_effort(work_type="question", override="not-a-level")
    # Falls through past the bad override to the work type's own default.
    assert profile.level == DEFAULT_EFFORT_BY_TYPE["question"]


def test_resolve_effort_unknown_work_type_degrades_to_global_default():
    profile, _ = resolve_effort(work_type="not-a-real-type")
    assert profile.level == GLOBAL_DEFAULT_EFFORT


def test_declared_cost_present_before_any_spend():
    # The whole invariant: a caller can read a real number BEFORE calling a
    # provider. It must be non-negative and present at every level.
    for level in EFFORT_LEVELS:
        profile, _ = resolve_effort(override=level)
        assert profile.estimated_cost_usd >= 0.0
