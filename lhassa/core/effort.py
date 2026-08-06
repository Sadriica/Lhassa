# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Effort levels — Plan 4, Stream 1's third axis (the versatile brain).

Alongside task identification (``notify.intent.classify_work_type``) and
working mode (``notify.modes``), effort answers "how MUCH" — token budget,
loop turns, verifier count, whether a MoA panel opens. It is pure, $0,
deterministic policy: a dataclass table plus a resolver, no model call, no I/O.
Lives in ``core/`` (the spine) rather than a shell because it is a self-
contained numeric policy with no shell dependency, exactly like
``core/policy.py``'s ``context_tier``/``hard`` — which this module is meant to
sit beside, not replace.

The hard invariant this module exists to enforce: **effort is DECLARED before
it is spent, never discovered by spending.** :func:`resolve_effort` runs
BEFORE any provider/harness call and returns a profile a caller can log into
an Episode and use to size the actual request (``max_tokens``, loop
``max_turns``, how many verifiers to run, whether to open a MoA panel) —
the declaration IS the budget, not a post-hoc description of what happened.

Four levels, cheapest first:
  ``minimal``    — a terse, single-pass answer. Smallest token budget, no loop,
                    no verifier, no MoA.
  ``standard``    — the default: a normal think, one verifier when the mode
                    calls for one, still no MoA.
  ``deep``        — a wider budget, a bounded loop, more verifiers, MoA opens
                    if the mode supports it.
  ``exhaustive``  — the ceiling: largest budget, the most loop turns, every
                    verifier the mode has, MoA always opens.

Settable three ways (in priority order, see :func:`resolve_effort`):
  1. an explicit ``override`` (e.g. a chat command line's ``--deep`` already
     parsed by the caller, or a programmatic caller that already knows).
  2. a ``--<level>`` marker inside the message text itself (parsed here,
     ``--minimal``/``--standard``/``--deep``/``--exhaustive``), stripped from
     the returned clean text.
  3. the work type's policy default (:data:`DEFAULT_EFFORT_BY_TYPE`).
  4. the global default, ``"standard"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "EFFORT_LEVELS",
    "EffortProfile",
    "DEFAULT_EFFORT_BY_TYPE",
    "GLOBAL_DEFAULT_EFFORT",
    "parse_effort_marker",
    "resolve_effort",
]

EFFORT_LEVELS: tuple[str, ...] = ("minimal", "standard", "deep", "exhaustive")

# A rough, deterministic $/1K-token planning estimate — NOT real metering (the
# actual cost comes back on the provider's response). It exists so a caller
# can DECLARE an order-of-magnitude cost before spending anything, matching
# the "cost declared before spending" invariant. Priced at a conservative
# blended rate; deliberately not wired to any live pricing table so this
# module has zero I/O and zero shell dependency.
_ESTIMATED_USD_PER_1K_TOKENS = 0.003


@dataclass(frozen=True)
class EffortProfile:
    """One effort level's governing numbers.

    ``level``              — one of :data:`EFFORT_LEVELS`.
    ``token_budget``        — the max-tokens ceiling for the think/reply.
    ``loop_turns``          — max iterations for a verifier-terminated loop
                               (``0`` means "no loop, single pass").
    ``verifier_count``      — how many verifiers a mode should run (``0`` means
                               "none" — some modes never verify regardless).
    ``moa``                 — whether a Mixture-of-Agents panel may open.
    ``estimated_cost_usd``  — a declared, pre-spend cost estimate (see the
                               module docstring — planning-only, not metering).
    """

    level: str
    token_budget: int
    loop_turns: int
    verifier_count: int
    moa: bool
    estimated_cost_usd: float


_PROFILES: dict[str, EffortProfile] = {
    "minimal": EffortProfile(
        level="minimal", token_budget=256, loop_turns=0, verifier_count=0,
        moa=False, estimated_cost_usd=round(256 / 1000 * _ESTIMATED_USD_PER_1K_TOKENS, 5),
    ),
    "standard": EffortProfile(
        level="standard", token_budget=1024, loop_turns=1, verifier_count=1,
        moa=False, estimated_cost_usd=round(1024 / 1000 * _ESTIMATED_USD_PER_1K_TOKENS, 5),
    ),
    "deep": EffortProfile(
        level="deep", token_budget=4096, loop_turns=3, verifier_count=2,
        moa=True, estimated_cost_usd=round(4096 / 1000 * _ESTIMATED_USD_PER_1K_TOKENS, 5),
    ),
    "exhaustive": EffortProfile(
        level="exhaustive", token_budget=16384, loop_turns=6, verifier_count=4,
        moa=True, estimated_cost_usd=round(16384 / 1000 * _ESTIMATED_USD_PER_1K_TOKENS, 5),
    ),
}

# Work type -> its policy-default effort level. Anything unlisted falls to
# :data:`GLOBAL_DEFAULT_EFFORT`. A "decision" defaults deeper than a plain
# "question" because a recommendation deserves more than one shallow pass, but
# NONE of them jump straight to "exhaustive" — that always requires either an
# explicit override or an in-message marker, never an implicit policy default.
DEFAULT_EFFORT_BY_TYPE: dict[str, str] = {
    "question": "minimal",
    "research": "standard",
    "build": "standard",
    "review": "standard",
    "maintenance": "minimal",
    "decision": "deep",
}

GLOBAL_DEFAULT_EFFORT = "standard"

_MARKER_RE = re.compile(r"--(minimal|standard|deep|exhaustive)\b", re.IGNORECASE)


def parse_effort_marker(text: str) -> tuple[Optional[str], str]:
    """Pull a ``--<level>`` marker out of ``text``.

    Returns ``(level_or_None, cleaned_text)`` — the marker is stripped from
    the returned text (surrounding whitespace collapsed) whether or not it
    matched a known level, so a mistyped marker never leaks into the actual
    prompt. Never raises: a falsy/non-string input returns ``(None, "")``.
    The FIRST marker wins if more than one is present.
    """
    try:
        raw = text or ""
    except Exception:
        return None, ""
    if not isinstance(raw, str):
        return None, ""

    match = _MARKER_RE.search(raw)
    level = match.group(1).lower() if match else None
    cleaned = _MARKER_RE.sub("", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return level, cleaned


def _profile_for(level: str) -> EffortProfile:
    return _PROFILES.get(level, _PROFILES[GLOBAL_DEFAULT_EFFORT])


def resolve_effort(
    *,
    work_type: Optional[str] = None,
    message: Optional[str] = None,
    override: Optional[str] = None,
) -> tuple[EffortProfile, str]:
    """Resolve the effort level BEFORE anything is spent. Never raises.

    Returns ``(profile, cleaned_message)`` — ``cleaned_message`` is ``message``
    with any ``--<level>`` marker stripped (identical to ``message`` when no
    marker was present or ``message`` was not given).

    Priority (first usable wins):
      1. ``override`` — an explicit, already-decided level.
      2. a ``--<level>`` marker found inside ``message``.
      3. ``work_type``'s entry in :data:`DEFAULT_EFFORT_BY_TYPE`.
      4. :data:`GLOBAL_DEFAULT_EFFORT` (``"standard"``).

    An unrecognized ``override`` or work type is treated as absent (falls
    through to the next priority) rather than raising — this axis degrades to
    the safe, mid-cost default, it never crashes a caller over a typo.
    """
    cleaned = message or ""
    marker_level, cleaned = parse_effort_marker(message) if message else (None, cleaned)

    if override and override in _PROFILES:
        return _profile_for(override), cleaned
    if marker_level and marker_level in _PROFILES:
        return _profile_for(marker_level), cleaned
    if work_type and work_type in DEFAULT_EFFORT_BY_TYPE:
        return _profile_for(DEFAULT_EFFORT_BY_TYPE[work_type]), cleaned
    return _profile_for(GLOBAL_DEFAULT_EFFORT), cleaned
