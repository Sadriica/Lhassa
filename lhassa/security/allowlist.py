"""The deny-by-default order-execution wall (§2.6, wall #2).

This is the machine's guard: a task runs ONLY if it clears every check. Nothing
is allowed unless it is explicitly listed — the empty case denies.

HITL per flow, NOT global: the approval threshold lives PER ACTION, by risk
(``min_trust`` + ``requires_approval``), not one global knob. Moving money or
deleting data can demand human approval while a draft does not.

Relation to ``lhassa.tools.privileged``: that module is a separate, command-level
(sudo) allowlist over OS binaries. THIS allowlist is task-level — it gates which
signed ``Task`` actions execute. Independent layers, kept separate on purpose.

Mirrors the brief's pseudocode:

    def allow(task) -> bool:
        return (task.accion in ALLOWLIST
                and task.objetivo in ALLOWLIST[task.accion].objetivos
                and task.origen.trust >= ALLOWLIST[task.accion].min_trust
                and signing.verify(task))

``allow()`` returns a richer ``AllowDecision`` (with a reason and the per-action
approval flag) but checks the same conditions in the same order.
"""

from dataclasses import dataclass

from lhassa.config import Config
from lhassa.core.task import Task
from lhassa.security import signing
from lhassa.security.trust import TrustLevel

# A sentinel meaning "every objetivo is allowed for this action" (dev default).
WILDCARD = "*"


@dataclass
class ActionSpec:
    """Per-action policy: which targets, how much trust, and whether HITL applies."""

    objetivos: "set[str] | str"  # a set of allowed objetivos, or WILDCARD ("*")
    min_trust: TrustLevel
    requires_approval: bool


@dataclass
class AllowDecision:
    """Outcome of the gate: allowed?, why, and whether human approval is required."""

    allowed: bool
    reason: str
    requires_approval: bool = False


def _config_repos() -> "set[str] | str":
    """Allowed repo names for ``sync_repo`` from config (``WILDCARD`` in dev)."""
    return Config.allowlist_repos


def build_allowlist(repos: "set[str] | str | None" = None) -> dict[str, ActionSpec]:
    """Build the action policy table. ``repos`` overrides the ``sync_repo`` targets.

    Phase-0 has exactly one entry: ``sync_repo`` (the first typed task, §2.5).
    """
    if repos is None:
        repos = _config_repos()
    return {
        "sync_repo": ActionSpec(
            objetivos=repos,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
    }


# The canonical policy table. Rebuild via ``build_allowlist`` if config changes.
ALLOWLIST: dict[str, ActionSpec] = build_allowlist()


def _objetivo_allowed(objetivo: str, objetivos: "set[str] | str") -> bool:
    """True if ``objetivo`` is permitted (WILDCARD, or membership in the set)."""
    if objetivos == WILDCARD:
        return True
    if isinstance(objetivos, str):
        return objetivo == objetivos
    return WILDCARD in objetivos or objetivo in objetivos


def allow(task: Task, *, public_key=None) -> AllowDecision:
    """Decide whether a machine may execute ``task``. Deny-by-default.

    Checks, in the brief's order — action listed → objetivo allowed →
    trust >= min_trust → signature verifies. The first failure denies with a
    clear reason. ``public_key`` defaults to the local public key
    (``signing.load_public_key()``); pass one to pin a specific brain identity.
    """
    spec = ALLOWLIST.get(task.accion)
    if spec is None:
        return AllowDecision(False, f"action '{task.accion}' is not in the allowlist")

    if not _objetivo_allowed(task.objetivo, spec.objetivos):
        return AllowDecision(
            False,
            f"objetivo '{task.objetivo}' is not allowed for action '{task.accion}'",
            spec.requires_approval,
        )

    if task.origen.trust < spec.min_trust:
        return AllowDecision(
            False,
            f"trust {task.origen.trust.name} is below required {spec.min_trust.name} "
            f"for action '{task.accion}'",
            spec.requires_approval,
        )

    pk = public_key if public_key is not None else signing.load_public_key()
    if not signing.verify_task(task, pk):
        return AllowDecision(
            False,
            "signature verification failed",
            spec.requires_approval,
        )

    return AllowDecision(True, "allowed", spec.requires_approval)
