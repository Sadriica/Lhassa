# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Working modes. Plan 4, Stream 1's second axis (the versatile brain).

``notify.intent.classify_work_type`` answers "what KIND of work is this?" ($0,
regex-only). This module answers the next question: "given that kind, HOW
should Lhassa go about it?": a small, pluggable registry of *ways of working*,
adopting the same :class:`~lhassa.registry.Registry` pattern as every other
swap point in the codebase (providers, harnesses, verifiers, channels,
actions): a mode is a drop-in (``@register_mode("name")``), never a core edit.

Five built-in modes cover the six work types:

  ``answer_directly``     : a bounded, self-contained think. No tools, no
                              delegation. The default for ``question``.
  ``search_and_cite``     : routes through the research bridge (a real web
                              lookup, sourced). For ``research``.
  ``iterate_with_verifier``: a bounded, verifier-terminated loop (mirrors
                              ``agents/author.py``'s propose/verify/retry
                              shape). For ``build`` and ``review``.
  ``delegate_to_hierarchy``: hands the work down the org chart (an
                              ``org_run`` task) rather than thinking it
                              through inline. For ``maintenance``.
  ``open_flow``            : opens a work-flow (trigger -> task -> gate ->
                              output) instead of a single think. For
                              ``decision`` (a choice worth a structured,
                              gated process rather than one opinion).

This module resolves ONLY the *name* of a mode for a work type; it never
calls a provider, launches a harness, or mints a task itself; wiring a mode's
name to the actual execution path (chat think / research marker / org_run /
flow run) is the caller's job, exactly like ``llm/router.py`` resolving a
provider NAME is separate from actually calling ``provider.complete()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lhassa.registry import Registry

__all__ = [
    "WorkingMode",
    "MODES",
    "register_mode",
    "WORK_TYPE_MODE",
    "resolve_mode",
    "DEFAULT_MODE",
]


@dataclass(frozen=True)
class WorkingMode:
    """One registered way of working.

    ``name``       : the registry key, also the stable identifier logged into
                       an Episode's ``decision``/``accion`` fields.
    ``description``: one line, human-readable (surfaced in ``/capabilities``-
                       style listings and test assertions).
    ``tags``       : free-form hints for callers (e.g. ``{"tools": True}``);
                       purely descriptive, never interpreted by this module.
    """

    name: str
    description: str
    tags: frozenset[str] = field(default_factory=frozenset)


MODES: Registry[WorkingMode] = Registry(kind="mode")


def register_mode(name: str, mode: "WorkingMode | None" = None, *, override: bool = False):
    """Register a working mode under ``name``. Direct call or decorator target.

    The drop-in seam: a plugin can add a NEW way of working (e.g. a future
    ``pair_program`` mode) without touching this module, exactly like
    ``@register_provider``/``@register_verifier``/``@register_channel``.
    """
    return MODES.register(name, mode, override=override)


# ── built-in modes ────────────────────────────────────────────────────────────

register_mode(
    "answer_directly",
    WorkingMode(
        name="answer_directly",
        description="One bounded, tool-less think; no delegation, no loop.",
        tags=frozenset({"single_turn"}),
    ),
)
register_mode(
    "search_and_cite",
    WorkingMode(
        name="search_and_cite",
        description="Route through the research bridge; a real web lookup, sourced.",
        tags=frozenset({"single_turn", "external_lookup"}),
    ),
)
register_mode(
    "iterate_with_verifier",
    WorkingMode(
        name="iterate_with_verifier",
        description="A bounded, verifier-terminated loop (propose -> verify -> retry).",
        tags=frozenset({"loop", "verified"}),
    ),
)
register_mode(
    "delegate_to_hierarchy",
    WorkingMode(
        name="delegate_to_hierarchy",
        description="Hand the work down the org chart instead of thinking it inline.",
        tags=frozenset({"delegated"}),
    ),
)
register_mode(
    "open_flow",
    WorkingMode(
        name="open_flow",
        description="Open a work-flow (trigger -> task -> gate -> output) for the ask.",
        tags=frozenset({"structured"}),
    ),
)

# The safest, cheapest fallback when a work type has no mapped mode, or the
# mapped mode was never registered (a plugin removed/misnamed it); never a
# KeyError, never a silently "bigger" mode than what was asked for.
DEFAULT_MODE = "answer_directly"

# The default work-type -> mode map. A plugin may override an ENTRY (not the
# whole dict) via :func:`resolve_mode`'s ``overrides`` parameter, or replace
# this module-level mapping directly for a deployment-wide policy change.
WORK_TYPE_MODE: dict[str, str] = {
    "question": "answer_directly",
    "research": "search_and_cite",
    "build": "iterate_with_verifier",
    "review": "iterate_with_verifier",
    "maintenance": "delegate_to_hierarchy",
    "decision": "open_flow",
}


def resolve_mode(work_type: str, *, overrides: "dict[str, str] | None" = None) -> WorkingMode:
    """Resolve the working mode for a work type. Never raises.

    Looks up ``overrides`` (if given) then :data:`WORK_TYPE_MODE` for the mode
    NAME, then :data:`MODES` for the registered :class:`WorkingMode`. An
    unknown work type, an unmapped-to mode, or an unregistered mode name all
    degrade to :data:`DEFAULT_MODE` (``answer_directly``) rather than raising;
    the working-mode axis must never crash the caller.
    """
    table = overrides if overrides else WORK_TYPE_MODE
    name = table.get(work_type) or WORK_TYPE_MODE.get(work_type) or DEFAULT_MODE
    mode = MODES.get_or_none(name)
    if mode is None:
        mode = MODES.get_or_none(DEFAULT_MODE)
    if mode is None:
        # Even the default was somehow deregistered; synthesize a bare mode
        # rather than raise; this axis degrades, it never crashes a caller.
        return WorkingMode(name=DEFAULT_MODE, description="fallback: no modes registered")
    return mode
