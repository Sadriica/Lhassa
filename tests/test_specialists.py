"""Tests for lhassa.agents.specialists — domain skills (NOT fine-tuning).

Offline and model-agnostic: a specialist is base-model + instructions + tools +
gate criteria, so these assertions never touch a model or the network.
"""

from lhassa import persona
from lhassa.agents import specialists
from lhassa.agents.specialists import (
    OPTIMIZACION,
    Specialist,
    build_specialist_prompt,
    get_specialist,
    list_specialists,
)


def test_first_specialist_is_optimizacion():
    s = get_specialist("optimizacion")
    assert s is not None
    assert isinstance(s, Specialist)
    assert s is OPTIMIZACION
    assert s.domain == "optimizacion"
    # The first domain has a clean lower-is-better metric so "mejor" is provable.
    assert s.metric_name
    assert s.gate_spec.get("higher_is_better") is False


def test_list_specialists_includes_optimizacion():
    domains = {s.domain for s in list_specialists()}
    assert "optimizacion" in domains


def test_unknown_domain_returns_none():
    assert get_specialist("does-not-exist") is None


def test_gate_spec_wires_into_gates_shape():
    # The gate_spec carries the criteria gates.run_gates expects: a tests_spec for
    # "correcta" and a metric direction for "mejor".
    s = get_specialist("optimizacion")
    assert "tests_spec" in s.gate_spec
    assert "higher_is_better" in s.gate_spec


def test_build_prompt_includes_persona_and_instructions():
    s = get_specialist("optimizacion")
    prompt = build_specialist_prompt(s)
    identity = persona.load_identity()
    # Identity first (so it still sounds like the same assistant), then the skill.
    assert identity.persona_block in prompt
    assert s.instructions in prompt
    assert prompt.index(identity.persona_block) < prompt.index(s.instructions)


def test_build_prompt_is_model_agnostic():
    # Skills, NOT fine-tuning: no model id leaks into the prompt or the dataclass.
    s = get_specialist("optimizacion")
    prompt = build_specialist_prompt(s).lower()
    for token in ("claude", "gpt", "llama", "fine-tune", "fine tune"):
        assert token not in prompt


def test_specialist_is_frozen():
    import dataclasses

    s = get_specialist("optimizacion")
    try:
        s.name = "mutated"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("Specialist should be frozen/immutable")


def test_module_reexports():
    assert specialists.get_specialist("optimizacion") is OPTIMIZACION
