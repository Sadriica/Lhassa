# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``temple`` seed. Lhassa's own inner workshop for INTERNAL functions.

Mirrors :mod:`lhassa.seeds.cv`'s topology-provisioning shape (idempotent, goes
through the validated org management API): a single department, ``temple``, a
DIRECT child of Lhassa (``config.root_admin_id``); distinct from ``interns``
(the CV pipeline's workers) and ``personal`` (the owner's own department);
plus its six leaf WORKER brains (the "monks"), each owning a sub-domain:

  - ``scribe``      : docs (documentation upkeep).
  - ``artificer``      : internal creation (tools/skills/flows authoring, the
    self-improvement author).
  - ``synchronizer``: sync (repo/calendar/memory).
  - ``guardian``      : self-maintenance (curate/review/distill).
  - ``oracle``        : the consciousness (repetition detection, the
    self-routing/self-improvement proposals).
  - ``geshe``         : the research line (the method: how Lhassa writes,
    drafts, builds and focuses its research; evidence-cited notes).

:mod:`lhassa.org.temple` owns the ACTION→WORKER ownership map
(:data:`lhassa.org.temple.ACTION_OWNERS`) and the down-execute-up loop that
routes each of the five maintenance actions through its owning monk; this
module is only responsible for CREATING the department and its workers (this
is where every tool's home in the hierarchy is provisioned; see
:mod:`lhassa.seeds.maintenance` for the recurring hygiene tasks that flow
through them).

Idempotent (an existing brain (department or worker) is left untouched) and
non-destructive (a partial failure is reported under ``errors``, never fatal).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.org import management
from lhassa.org.temple import TEMPLE_WORKERS as _TEMPLE_WORKERS
from lhassa.org.topology import load_topology
from lhassa.seeds import register_seed

if TYPE_CHECKING:
    from lhassa.config import Config

#: The temple brain id + the department name/identity it owns.
TEMPLE_ID = "temple"
TEMPLE_DEPARTMENT = "temple"
#: A distinct role label. Lhassa's inner temple, not a generic "department".
TEMPLE_ROLE = "internal-operations"
#: The domains temple handles; docs, internal creation, sync, self-maintenance.
TEMPLE_SKILLS: tuple[str, ...] = (
    "docs",
    "internal-creation",
    "sync",
    "self-maintenance",
)

#: The five worker brains ("monks") created under temple; the single source
#: of truth for their ids is lhassa.org.temple (which also owns the ACTION→
#: WORKER ownership map that routes work to them).
TEMPLE_WORKERS: tuple[str, ...] = _TEMPLE_WORKERS
#: A distinct role label for a worker brain; a leaf that executes, not a
#: coordinating department.
TEMPLE_WORKER_ROLE = "monk"

#: Per-worker skill assignments. Most monks carry none (their domain is the
#: action-ownership map); the GESHE carries the research-method skill; the
#: 3-layer skill pattern: the NAME here → ~/.lhassa/skills/research-method/
#: SKILL.md → loaded into its prompt at think time, so every geshe think
#: writes and focuses research the house way.
WORKER_SKILLS: dict[str, tuple[str, ...]] = {
    "geshe": ("research-method",),
}


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


def seed_temple(config: "Optional[Config]" = None) -> dict:
    """Create the ``temple`` department AND its five worker brains. Idempotent.

    Returns::

        {
          "brain": "temple", "created": bool, "existing": bool,
          "workers_created": [...], "workers_existing": [...], "errors": [],
        }

    ``created``/``existing`` describe the department itself (back-compatible
    with the pre-workers seed shape); the workers are reported separately since
    a fresh env creates the department AND all five monks in one pass, while a
    later re-run of the seed (department already existing) still creates any
    monk that is still missing (e.g. after a partial prior run). Never raises
    destructively; a failure is recorded under ``errors``, never fatal.
    """
    cfg = _resolve_config(config)
    summary: dict = {
        "brain": TEMPLE_ID,
        "created": False,
        "existing": False,
        "workers_created": [],
        "workers_existing": [],
        "errors": [],
    }
    try:
        if load_topology(cfg).has(TEMPLE_ID):
            summary["existing"] = True
        else:
            management.add_brain(
                TEMPLE_ID,
                role=TEMPLE_ROLE,
                # parent=None attaches directly under the permanent administrator
                # (Lhassa) via the org management API; temple is Lhassa's OWN
                # inner department, a sibling of interns/personal, not nested
                # under either.
                parent=None,
                department=TEMPLE_DEPARTMENT,
                skills=TEMPLE_SKILLS,
                config=cfg,
            )
            summary["created"] = True
    except Exception as exc:  # a partial failure is reported, not fatal
        logger.warning("seed[temple]: {}", exc)
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
        return summary  # no department to hang workers under; stop here

    # The five monks; each a leaf under temple, sharing its department label
    # (mirrors lhassa.seeds.cv's worker brains, which share INTERNS_DEPARTMENT
    # too); lhassa.org.temple resolves a SPECIFIC monk by its own id, not by
    # this shared department string, so the sharing is harmless.
    for worker_id in TEMPLE_WORKERS:
        try:
            created = _ensure_brain(
                worker_id,
                role=TEMPLE_WORKER_ROLE,
                parent=TEMPLE_ID,
                department=TEMPLE_DEPARTMENT,
                skills=WORKER_SKILLS.get(worker_id, ()),
                config=cfg,
            )
            key = "workers_created" if created else "workers_existing"
            summary[key].append(worker_id)
        except Exception as exc:  # a partial failure is reported, not fatal
            logger.warning("seed[temple]: worker {} failed: {}", worker_id, exc)
            summary["errors"].append(f"{worker_id}: {type(exc).__name__}: {exc}")

    return summary


@register_seed(
    "temple",
    "Create the 'temple' department and its six worker brains (scribe/"
    "artificer/synchronizer/guardian/oracle/geshe); Lhassa's own inner workshop for "
    "internal functions (docs, internal creation, synchronization, "
    "self-maintenance), a direct child of Lhassa in the topology.",
)
def apply(config: "Optional[Config]" = None) -> dict:
    """The ``temple`` seed entry point; see :func:`seed_temple`."""
    return seed_temple(config)
