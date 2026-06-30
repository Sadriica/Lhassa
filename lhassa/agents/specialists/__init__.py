"""Domain specialists — **skills, NOT fine-tuning** (§2.8).

A *specialist* is the same base model the rest of the brain uses, plus three things
that are pure data: domain **instructions** (system text appended to the persona),
the **tools** it is allowed to reach for, and the **gate criteria** (tests/metric)
its self-written candidates must pass to graduate. There is NO model weight change,
no fine-tune, no per-domain checkpoint — so a specialist depends on NO specific
model and travels intact if the base model is swapped.

This mirrors the brief: "Especialistas por dominio = skills, NO fine-tuning (mismo
modelo base + instrucciones + herramientas + criterios; así no dependes de ningún
modelo). Compuertas modulares por dominio; deny-by-default. Primer dominio =
optimización (métrica limpia)."

Public surface:
    Specialist                — the dataclass (instructions + tools + gate_spec).
    get_specialist(domain)    — look one up by domain.
    list_specialists()        — every registered specialist.
    build_specialist_prompt() — persona identity + the specialist's instructions.
"""

from lhassa.agents.specialists.registry import (
    OPTIMIZACION,
    Specialist,
    build_specialist_prompt,
    get_specialist,
    list_specialists,
)

__all__ = [
    "OPTIMIZACION",
    "Specialist",
    "build_specialist_prompt",
    "get_specialist",
    "list_specialists",
]
