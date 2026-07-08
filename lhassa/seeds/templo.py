"""The ``templo`` seed — Lhassa's own inner workshop for INTERNAL functions.

Mirrors :mod:`lhassa.seeds.cv`'s topology-provisioning shape (idempotent, goes
through the validated org management API), but scaffolds a single department:
``templo``, a DIRECT child of Lhassa (``config.root_admin_id``) — distinct from
``interns`` (the CV pipeline's workers) and ``personal`` (the owner's own
department). Its domain: docs, internal creation (tools/skills/flows authoring,
the consciousness proposals), synchronization (repo/calendar/memory), and
self-maintenance (curate/review/detect_repetition/distill) — see
:mod:`lhassa.org.temple` for the down-execute-up loop that routes work TO it,
and :mod:`lhassa.seeds.maintenance` for the recurring hygiene tasks that now
flow through it.

Idempotent (an existing ``templo`` brain is left untouched) and non-destructive
(a partial failure is reported under ``errors``, never fatal).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.org import management
from lhassa.org.topology import load_topology
from lhassa.seeds import register_seed

if TYPE_CHECKING:
    from lhassa.config import Config

#: The templo brain id + the department name/identity it owns.
TEMPLO_ID = "templo"
TEMPLO_DEPARTMENT = "templo"
#: A distinct role label — Lhassa's inner temple, not a generic "department".
TEMPLO_ROLE = "internal-operations"
#: The domains templo handles — docs, internal creation, sync, self-maintenance.
TEMPLO_SKILLS: tuple[str, ...] = (
    "docs",
    "internal-creation",
    "sync",
    "self-maintenance",
)


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def seed_templo(config: "Optional[Config]" = None) -> dict:
    """Create the ``templo`` department under Lhassa. Idempotent.

    Returns ``{"brain": "templo", "created": bool, "existing": bool, "errors": []}``.
    Never raises destructively — a failure is recorded under ``errors``.
    """
    cfg = _resolve_config(config)
    summary: dict = {
        "brain": TEMPLO_ID,
        "created": False,
        "existing": False,
        "errors": [],
    }
    try:
        if load_topology(cfg).has(TEMPLO_ID):
            summary["existing"] = True
        else:
            management.add_brain(
                TEMPLO_ID,
                role=TEMPLO_ROLE,
                # parent=None attaches directly under the permanent administrator
                # (Lhassa) via the org management API — templo is Lhassa's OWN
                # inner department, a sibling of interns/personal, not nested
                # under either.
                parent=None,
                department=TEMPLO_DEPARTMENT,
                skills=TEMPLO_SKILLS,
                config=cfg,
            )
            summary["created"] = True
    except Exception as exc:  # a partial failure is reported, not fatal
        logger.warning("seed[templo]: {}", exc)
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    return summary


@register_seed(
    "templo",
    "Create the 'templo' department — Lhassa's own inner workshop for internal "
    "functions (docs, internal creation, synchronization, self-maintenance), a "
    "direct child of Lhassa in the topology.",
)
def apply(config: "Optional[Config]" = None) -> dict:
    """The ``templo`` seed entry point — see :func:`seed_templo`."""
    return seed_templo(config)
