"""Tests for lhassa.agents.specialists — domain skills (NOT fine-tuning).

Offline and model-agnostic: a specialist is base-model + instructions + tools +
gate criteria, so these assertions never touch a model or the network.
"""

import pytest

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


# ── keystone registry: introspection + drop-in seam ──────────────────────────


def test_specialists_registry_exposes_optimizacion():
    from lhassa.agents.specialists import registry as reg
    from lhassa.registry import Registry

    assert isinstance(reg.SPECIALISTS, Registry)
    assert "optimizacion" in reg.SPECIALISTS.names()
    # The legacy ``_REGISTRY`` alias is a live read-only view of the same store.
    assert reg._REGISTRY.get("optimizacion") is OPTIMIZACION


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

    clash = Specialist(name="x", domain="optimizacion", instructions="y")
    with pytest.raises(ValueError, match="already registered"):
        reg.register_specialist(clash)
    # override=True replaces without raising; restore the original afterwards.
    try:
        reg.register_specialist(clash, override=True)
        assert get_specialist("optimizacion") is clash
    finally:
        reg.register_specialist(OPTIMIZACION, override=True)
        assert get_specialist("optimizacion") is OPTIMIZACION
