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

    Phase-0 entry: ``sync_repo`` (the first typed task, §2.5).
    Phase-3 entries: the 15 actionpack actions.

    Policy principles:
    - Most actions: INTERNO trust, no approval, wildcard objetivos.
    - ``run_command``: INTERNO trust but ``requires_approval=True`` (high-risk;
      executes arbitrary shell code even if sandboxed).
    - ``tracking_postulaciones``: INTERNO trust, wildcard objetivos (Phase 0).
    """
    if repos is None:
        repos = _config_repos()
    return {
        # ── Phase 0 ───────────────────────────────────────────────────────────
        "sync_repo": ActionSpec(
            objetivos=repos,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "tracking_postulaciones": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        # ── Phase 3 — actionpack ──────────────────────────────────────────────
        "notify": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "add_task": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "add_fact": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "web_fetch": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "run_command": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=True,   # high-risk: execute arbitrary shell code
        ),
        "compile_cv": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "backup_memory": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "check_health": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "system_snapshot": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "git_status_repos": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "consolidate_memory": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "distill_memory": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "set_task_state": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "power_mode": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "list_approvals": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "ping_url": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "check_spend": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        # ── Self-improvement (§2.8, Etapa 4 — visible self-improvement) ──────────
        # Both are deterministic detection/gate passes; neither calls an LLM.
        # detect_repetition only PARKS an approval (mint_action) — it never
        # mints/executes anything itself, so no HITL gate is needed here.
        "detect_repetition": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "review_tools": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        "curate_skills": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,  # deterministic sweep; never deletes, pinned exempt
        ),
        "research_reply": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,  # read-only web search + a chat message
        ),
        "send_reminder": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,  # a notification the owner scheduled themselves
        ),
        # ── PTC — Programmatic Tool Calling (script-over-RPC in quarantine) ──
        "run_script": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            # High-risk like run_command: one script drives MANY actions via RPC.
            # The sandbox + per-call allowlist re-check still apply, but the entry
            # ticket into PTC always crosses the approval frontier.
            requires_approval=True,
        ),
        # ── Etapa 2 — operative day via Telegram ─────────────────────────────
        "send_digest": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        # ── Master calendar reconcile — deterministic $0 diff + a delivered
        # .ics per unsynced event; never writes to any calendar directly.
        "calendar_sync": ActionSpec(
            objetivos=WILDCARD,
            min_trust=TrustLevel.INTERNO,
            requires_approval=False,
        ),
        # ── Etapa 3 — real delegation into the org hierarchy ─────────────────
        # ``requires_approval`` stays False here: org_run's OWN high-risk gate
        # lives in params (see actionpack._org.org_run / org.coordinator.run_goal)
        # — a per-params flag, not a blanket per-action one. A high-risk goal
        # parks its own multi-person approval inside the org layer and refuses
        # to self-approve; this allowlist entry only governs whether org_run may
        # be minted at all.
        "org_run": ActionSpec(
            objetivos=WILDCARD,
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
