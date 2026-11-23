# SPDX-License-Identifier: AGPL-3.0-or-later
"""The specialist registry; skills keyed by domain (§2.8).

A :class:`Specialist` is **not** a fine-tuned model. It is the base model the whole
brain already runs, parametrised by data:

  * ``instructions``: domain system text appended to the loaded persona, so a
    specialist is *identity + a domain skill* and stays model-agnostic.
  * ``tools``       : the names of the tools this skill is allowed to use
    (deny-by-default: the domain gate is modular, only these are in scope).
  * ``gate_spec``   : the concrete criteria handed to :mod:`lhassa.agents.gates`:
    the ``tests_spec`` (correcta) and the ``metric_name``/baseline direction
    (mejor). The gates themselves stay objective and never call an LLM.

Because every part is data, swapping the base model changes nothing here; that is
the whole point of "skills, NO fine-tuning … así no dependes de ningún modelo".

The **first domain is ``optimization``**: a clean, lower-is-better metric
(tokens/tiempo/pasos) so "mejor" is provable. Fuzzier domains come later; each new
domain is just another ``register_specialist(...)`` call, no new code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lhassa import persona
from lhassa.registry import Registry


@dataclass(frozen=True)
class Specialist:
    """One domain skill: base model + instructions + tools + gate criteria.

    Skills, NOT fine-tuning. Nothing here references a specific model; a
    Specialist is a bundle of *data* (text, tool names, criteria) layered on top
    of whatever base model the brain is configured with. Frozen so the registry's
    shared instances can't be mutated by a caller.

    ``name``        : human-facing id of the skill.
    ``domain``      : the dominio it owns (the registry key; gates are modular
                       per domain; see :mod:`lhassa.agents.gates`).
    ``instructions``: system text appended to the persona block to specialise it.
    ``tools``       : allowed tool names (deny-by-default; only these are in scope).
    ``gate_spec``   : criteria for :func:`lhassa.agents.gates.run_gates`. Keys:
                       ``tests_spec`` (gate 2; correcta) and
                       ``higher_is_better`` (metric direction for gate 3; mejor;
                       optimization is lower-is-better → ``False``).
    ``metric_name`` : the concrete metric the "mejor" gate measures
                       (e.g. ``"tokens"`` / ``"tiempo"`` / ``"pasos"``).
    """

    name: str
    domain: str
    instructions: str
    tools: tuple[str, ...] = field(default_factory=tuple)
    gate_spec: dict = field(default_factory=dict)
    metric_name: str = ""


# ── First specialist: optimization (the clean-metric domain) ──────────────────
# Lower-is-better on a concrete metric (tokens/tiempo/pasos), so the "mejor" gate
# is decidable against a baseline. Its gate_spec wires straight into gates.run_gates
# / gates.promote: tests_spec proves "correcta", higher_is_better=False is the
# lower-is-better direction MetricVerifier uses for "mejor".
OPTIMIZATION = Specialist(
    name="optimizer",
    domain="optimization",
    instructions=(
        "You are operating as the OPTIMIZATION specialist. Your job is to make an "
        "existing routine cheaper on one clean, lower-is-better metric; tokens, "
        "wall-clock time, or number of steps; without changing what it produces.\n"
        "- Always measure against a concrete baseline; never claim 'better' without "
        "a number that beats it. No baseline means you cannot prove an improvement.\n"
        "- Prefer the smallest change that moves the metric. Do not trade "
        "correctness for speed: a faster wrong answer fails the gates.\n"
        "- Report the metric before and after, plainly."
    ),
    tools=("read_file", "write_file", "shell"),
    gate_spec={
        # Gate 2 (correcta): a command/test verifier must exit 0. The concrete
        # spec for a given candidate is supplied at promote time; this is the shape.
        "tests_spec": {"type": "command"},
        # Gate 3 (mejor): optimization metrics are lower-is-better.
        "higher_is_better": False,
    },
    metric_name="tokens",
)


# Registry keyed by domain, backed by the shared keystone ``Registry`` (the same
# primitive that backs actions/channels/commands). Adding a domain = one
# ``register_specialist(...)`` call; a plugin can drop in a new domain WITHOUT
# editing this file (the elastic/modular principle), and a duplicate domain is
# rejected instead of silently clobbering.
SPECIALISTS: Registry[Specialist] = Registry("specialist")


def register_specialist(specialist: Specialist, *, override: bool = False) -> Specialist:
    """Register ``specialist`` under its ``.domain``: a wrapper over :data:`SPECIALISTS`.

    A duplicate domain raises unless ``override=True``: so two plugins can never
    silently clobber each other. Returns the specialist unchanged. This is the
    drop-in seam for adding a domain skill without touching core.
    """
    SPECIALISTS.register(specialist.domain, specialist, override=override)
    return specialist


# Register the first built-in domain through the seam.
register_specialist(OPTIMIZATION)

# Backward-compatible alias: a LIVE, read-only ``{domain: specialist}`` view of the
# registry's backing store, so any historical consumer doing ``_REGISTRY.get(domain)``
# keeps working while later ``register_specialist`` calls are reflected.
_REGISTRY = SPECIALISTS.as_mapping()


def get_specialist(domain: str) -> Optional[Specialist]:
    """Return the specialist owning ``domain``, or ``None`` if none is registered."""
    return SPECIALISTS.get_or_none(domain)


def list_specialists() -> list[Specialist]:
    """Every registered specialist (insertion order; optimization first)."""
    return list(SPECIALISTS.all())


def build_specialist_prompt(
    specialist: Specialist, instance_id: Optional[str] = None
) -> str:
    """Persona identity + the specialist's domain instructions.

    A specialist is *identity + a domain skill*: we load the running instance's
    persona block (so it still sounds like the same assistant) and append the
    specialist's instructions. No model is named anywhere; the prompt is the only
    thing that specialises the base model, which is what makes specialists
    skills-not-fine-tuning and model-agnostic.
    """
    identity = persona.load_identity(instance_id)
    return f"{identity.persona_block}\n\n{specialist.instructions}"
