# SPDX-License-Identifier: AGPL-3.0-or-later
"""Trust by origin + the WHO (§2.6).

Two orthogonal facts about where an action came from:
  - ``TrustLevel``: how much we trust the *source kind* (content vs. brain vs.
    a human). Ordered so comparisons (``>=``) are meaningful for thresholds.
  - ``Origin.actor_id``: *who* specifically (a person/agent id). Phase-0 only
    records it; it seeds the Phase-7 roles/RBAC layer without later surgery.

INVARIANT (principle #7): **trust degrades by contagion, never promotes.**
When several sources combine into one action, the result is only as trusted as
the *least* trusted input. A HUMANO directive that quotes MUNDO content is, for
that derived action, MUNDO. ``degrade()`` enforces this by returning the MINIMUM.
"""

from dataclasses import dataclass
from enum import IntEnum


class TrustLevel(IntEnum):
    """Trust of an action's source. Higher = more trusted (comparable)."""

    MUNDO = 0    # derived from external/untrusted content (a webpage, an email)
    INTERNO = 1  # the brain acting on itself, no external input
    HUMANO = 2   # a human, directly


@dataclass
class Origin:
    """Where an action came from: a trust level plus, optionally, the WHO.

    ``actor_id`` identifies the specific person/agent behind the action (not
    just how trustworthy it is); the seed for Phase-7 roles. ``None`` means
    "unattributed at this trust level".
    """

    trust: TrustLevel
    actor_id: str | None = None


def _as_level(item: "Origin | TrustLevel | int") -> TrustLevel:
    """Coerce an Origin / TrustLevel / int into a TrustLevel."""
    if isinstance(item, Origin):
        return item.trust
    return TrustLevel(int(item))


def degrade(*origins_or_levels: "Origin | TrustLevel | int") -> TrustLevel:
    """Combine sources into the trust of their derived action.

    Returns the MINIMUM trust among the inputs; trust degrades by contagion and
    is never promoted. Accepts ``Origin``, ``TrustLevel`` or plain ``int``.

    Raises ``ValueError`` if called with no inputs (there is nothing to trust).
    """
    if not origins_or_levels:
        raise ValueError("degrade() needs at least one origin or level")
    return min(_as_level(item) for item in origins_or_levels)
