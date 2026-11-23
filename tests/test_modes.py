"""Tests for the working-mode registry (lhassa/notify/modes.py, Plan 4 Stream 1)."""

import pytest

from lhassa.notify.modes import (
    DEFAULT_MODE,
    MODES,
    WORK_TYPE_MODE,
    WorkingMode,
    register_mode,
    resolve_mode,
)


def test_built_in_modes_are_registered():
    expected = {
        "answer_directly",
        "search_and_cite",
        "iterate_with_verifier",
        "delegate_to_hierarchy",
        "open_flow",
    }
    assert expected.issubset(set(MODES.names()))


def test_every_work_type_maps_to_a_registered_mode():
    for work_type, mode_name in WORK_TYPE_MODE.items():
        assert mode_name in MODES.names(), f"{work_type} -> unregistered mode {mode_name!r}"


def test_resolve_mode_matches_the_declared_mapping():
    assert resolve_mode("question").name == "answer_directly"
    assert resolve_mode("research").name == "search_and_cite"
    assert resolve_mode("build").name == "iterate_with_verifier"
    assert resolve_mode("review").name == "iterate_with_verifier"
    assert resolve_mode("maintenance").name == "delegate_to_hierarchy"
    assert resolve_mode("decision").name == "open_flow"


def test_resolve_mode_unknown_work_type_degrades_to_default():
    mode = resolve_mode("not-a-real-work-type")
    assert mode.name == DEFAULT_MODE


def test_resolve_mode_never_raises_on_bad_input():
    assert resolve_mode(None).name == DEFAULT_MODE  # type: ignore[arg-type]
    assert resolve_mode("").name == DEFAULT_MODE


def test_resolve_mode_respects_overrides():
    mode = resolve_mode("question", overrides={"question": "open_flow"})
    assert mode.name == "open_flow"


def test_resolve_mode_falls_back_when_override_points_at_unregistered_name():
    mode = resolve_mode("question", overrides={"question": "does_not_exist"})
    assert mode.name == DEFAULT_MODE


def test_register_mode_is_a_drop_in_seam():
    # A new mode registers without touching this module; the elastic/modular
    # pattern shared with providers/verifiers/channels/actions.
    name = "pair_program_test_only"
    try:
        register_mode(name, WorkingMode(name=name, description="test-only mode"))
        assert MODES.get(name).description == "test-only mode"
    finally:
        # Clean up so this test is repeatable across runs in one process.
        MODES._items.pop(name, None)  # noqa: SLF001; test-only cleanup


def test_register_mode_duplicate_without_override_raises():
    with pytest.raises(ValueError):
        register_mode("answer_directly", WorkingMode(name="answer_directly", description="dup"))
