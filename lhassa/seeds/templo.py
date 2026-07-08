"""The ``templo`` seed — Lhassa's own inner workshop for INTERNAL functions.

Mirrors :mod:`lhassa.seeds.cv`'s topology-provisioning shape (idempotent, goes
through the validated org management API): a single department, ``templo``, a
DIRECT child of Lhassa (``config.root_admin_id``) — distinct from ``interns``
(the CV pipeline's workers) and ``personal`` (the owner's own department) —
plus its five leaf WORKER brains (the "monks"), each owning a sub-domain:

  - ``escriba``       — docs (documentation upkeep).
  - ``artifice``       — internal creation (tools/skills/flows authoring, the
    self-improvement author).
  - ``sincronizador`` — sync (repo/calendar/memory).
  - ``guardian``       — self-maintenance (curate/review/distill).
  - ``oraculo``         — the consciousness (repetition detection, the
    self-routing/self-improvement proposals).

:mod:`lhassa.org.temple` owns the ACTION→WORKER ownership map
(:data:`lhassa.org.temple.ACTION_OWNERS`) and the down-execute-up loop that
routes each of the five maintenance actions through its owning monk; this
module is only responsible for CREATING the department and its workers (this
is where every tool's home in the hierarchy is provisioned — see
:mod:`lhassa.seeds.maintenance` for the recurring hygiene tasks that flow
through them).

Idempotent (an existing brain — department or worker — is left untouched) and
non-destructive (a partial failure is reported under ``errors``, never fatal).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.org import management
from lhassa.org.temple import TEMPLE_WORKERS
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

#: The five worker brains ("monks") created under templo — the single source
#: of truth for their ids is lhassa.org.temple (which also owns the ACTION→
#: WORKER ownership map that routes work to them).
TEMPLO_WORKERS: tuple[str, ...] = TEMPLE_WORKERS
#: A distinct role label for a worker brain — a leaf that executes, not a
#: coordinating department.
TEMPLO_WORKER_ROLE = "monk"


def _ensure_brain(
    node_id: str,
    *,
    role: str,
    parent: "Optional[str]",
    department: "Optional[str]" = None,
    skills: tuple[str, ...] = (),
    config: "Optional[Config]" = None,
) -> bool:
    """Create ``node_id`` if it is missing. Return ``True`` if created, else ``False``.

    Idempotent: an existing brain is left untouched (never re-created, never
    dropped). Mirrors :func:`lhassa.seeds.cv._ensure_brain` exactly.
    """
    if load_topology(config).has(node_id):
        return False
    management.add_brain(
        node_id,
        role=role,
        parent=parent,
        department=department,
        skills=skills,
        config=config,
    )
    return True


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def seed_templo(config: "Optional[Config]" = None) -> dict:
    """Create the ``templo`` department AND its five worker brains. Idempotent.

    Returns::

        {
          "brain": "templo", "created": bool, "existing": bool,
          "workers_created": [...], "workers_existing": [...], "errors": [],
        }

    ``created``/``existing`` describe the department itself (back-compatible
    with the pre-workers seed shape); the workers are reported separately since
    a fresh env creates the department AND all five monks in one pass, while a
    later re-run of the seed (department already existing) still creates any
    monk that is still missing (e.g. after a partial prior run). Never raises
    destructively — a failure is recorded under ``errors``, never fatal.
    """
    cfg = _resolve_config(config)
    summary: dict = {
        "brain": TEMPLO_ID,
        "created": False,
        "existing": False,
        "workers_created": [],
        "workers_existing": [],
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
        return summary  # no department to hang workers under — stop here

    # The five monks — each a leaf under templo, sharing its department label
    # (mirrors lhassa.seeds.cv's worker brains, which share INTERNS_DEPARTMENT
    # too); lhassa.org.temple resolves a SPECIFIC monk by its own id, not by
    # this shared department string, so the sharing is harmless.
    for worker_id in TEMPLO_WORKERS:
        try:
            created = _ensure_brain(
                worker_id,
                role=TEMPLO_WORKER_ROLE,
                parent=TEMPLO_ID,
                department=TEMPLO_DEPARTMENT,
                config=cfg,
            )
            key = "workers_created" if created else "workers_existing"
            summary[key].append(worker_id)
        except Exception as exc:  # a partial failure is reported, not fatal
            logger.warning("seed[templo]: worker {} failed: {}", worker_id, exc)
            summary["errors"].append(f"{worker_id}: {type(exc).__name__}: {exc}")

    return summary


@register_seed(
    "templo",
    "Create the 'templo' department and its five worker brains (escriba/"
    "artifice/sincronizador/guardian/oraculo) — Lhassa's own inner workshop for "
    "internal functions (docs, internal creation, synchronization, "
    "self-maintenance), a direct child of Lhassa in the topology.",
)
def apply(config: "Optional[Config]" = None) -> dict:
    """The ``templo`` seed entry point — see :func:`seed_templo`."""
    return seed_templo(config)
