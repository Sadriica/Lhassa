"""Tests for lhassa.agents.specialists; domain skills (NOT fine-tuning).

Offline and model-agnostic: a specialist is base-model + instructions + tools +
gate criteria, so these assertions never touch a model or the network.
"""

import pytest

from lhassa import persona
from lhassa.agents import specialists
from lhassa.agents.specialists import (
    OPTIMIZATION,
    Specialist,
    build_specialist_prompt,
    get_specialist,
    list_specialists,
)


def test_first_specialist_is_optimization():
    s = get_specialist("optimization")
    assert s is not None
    assert isinstance(s, Specialist)
    assert s is OPTIMIZATION
    assert s.domain == "optimization"
    # The first domain has a clean lower-is-better metric so "mejor" is provable.
    assert s.metric_name
    assert s.gate_spec.get("higher_is_better") is False


def test_list_specialists_includes_optimization():
    domains = {s.domain for s in list_specialists()}
    assert "optimization" in domains


def test_unknown_domain_returns_none():
    assert get_specialist("does-not-exist") is None


def test_gate_spec_wires_into_gates_shape():
    # The gate_spec carries the criteria gates.run_gates expects: a tests_spec for
    # "correcta" and a metric direction for "mejor".
    s = get_specialist("optimization")
    assert "tests_spec" in s.gate_spec
    assert "higher_is_better" in s.gate_spec


def test_build_prompt_includes_persona_and_instructions():
    s = get_specialist("optimization")
    prompt = build_specialist_prompt(s)
    identity = persona.load_identity()
    # Identity first (so it still sounds like the same assistant), then the skill.
    assert identity.persona_block in prompt
    assert s.instructions in prompt
    assert prompt.index(identity.persona_block) < prompt.index(s.instructions)


def test_build_prompt_is_model_agnostic():
    # Skills, NOT fine-tuning: no model id leaks into the prompt or the dataclass.
    s = get_specialist("optimization")
    prompt = build_specialist_prompt(s).lower()
    for token in ("claude", "gpt", "llama", "fine-tune", "fine tune"):
        assert token not in prompt


def test_specialist_is_frozen():
    import dataclasses

    s = get_specialist("optimization")
    try:
        s.name = "mutated"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("Specialist should be frozen/immutable")


def test_module_reexports():
    assert specialists.get_specialist("optimization") is OPTIMIZATION


# ── keystone registry: introspection + drop-in seam ──────────────────────────


def test_specialists_registry_exposes_optimization():
    from lhassa.agents.specialists import registry as reg
    from lhassa.registry import Registry

    assert isinstance(reg.SPECIALISTS, Registry)
    assert "optimization" in reg.SPECIALISTS.names()
    # The legacy ``_REGISTRY`` alias is a live read-only view of the same store.
    assert reg._REGISTRY.get("optimization") is OPTIMIZATION


def test_register_specialist_dropin_and_resolves():
    """A plugin can register a new domain without editing core, then resolve it."""
    from lhassa.agents.specialists import registry as reg

    dummy = Specialist(
        name="dummy-skill",
        domain="dummy-domain",
        instructions="Do the dummy thing.",
    )
    try:
        returned = reg.register_specialist(dummy)
        assert returned is dummy
        assert get_specialist("dummy-domain") is dummy
        assert dummy in list_specialists()
    finally:
        reg.SPECIALISTS.unregister("dummy-domain")


def test_register_specialist_override_protection():
    from lhassa.agents.specialists import registry as reg

    clash = Specialist(name="x", domain="optimization", instructions="y")
    with pytest.raises(ValueError, match="already registered"):
        reg.register_specialist(clash)
    # override=True replaces without raising; restore the original afterwards.
    try:
        reg.register_specialist(clash, override=True)
        assert get_specialist("optimization") is clash
    finally:
        reg.register_specialist(OPTIMIZATION, override=True)
        assert get_specialist("optimization") is OPTIMIZATION
