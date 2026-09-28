"""Parametrized identity; org hook #2 (§3-Fase 0): persona.py loads the
instance's identity at startup, not a constant. Backward-compat exports must stay
identical so the LLM path and the LLM-free alert path keep working unchanged.
"""

from lhassa import persona
from lhassa.persona import (
    Identity,
    PERSONA_BLOCK,
    PERSONA_NAME,
    format_alert,
    load_identity,
    system_persona,
)


def test_load_identity_returns_personal_identity():
    ident = load_identity("inst_personal")
    assert isinstance(ident, Identity)
    assert ident.name == "Lhassa"
    assert ident.gender_neutral is True
    assert ident.persona_block.startswith("You are Lhassa")
    # The two always-loaded DIRECTIVAS (Apéndice B).
    assert "responder conciso por defecto" in ident.directives
    assert "optimizar; muy sensible a cualquier delay" in ident.directives


def test_load_identity_defaults_and_falls_back():
    # No arg → default instance; unknown instance → falls back to default.
    assert load_identity().name == "Lhassa"
    assert load_identity("inst_does_not_exist").name == "Lhassa"


def test_load_identity_returns_a_fresh_copy():
    # Mutating a returned identity must not corrupt the registry.
    ident = load_identity("inst_personal")
    ident.directives.append("tampered")
    assert "tampered" not in load_identity("inst_personal").directives


def test_system_persona_appends_directives():
    sp = system_persona("inst_personal")
    assert PERSONA_BLOCK in sp
    assert "responder conciso por defecto" in sp
    assert "optimizar; muy sensible a cualquier delay" in sp


def test_backward_compatible_exports_unchanged():
    # The legacy constants are derived from the default identity and unchanged.
    assert PERSONA_NAME == "Lhassa"
    assert PERSONA_BLOCK == load_identity("inst_personal").persona_block
    assert persona.PERSONA_NAME == "Lhassa"
    # STYLE_GUIDE + format_alert still present and behaving as before.
    assert "humble" in persona.STYLE_GUIDE["tone"]  # the tone evolved; humility is permanent
    assert format_alert("disk almost full", level="warn") == "Heads up: disk almost full"
    assert format_alert("all good", level="info") == "all good"
