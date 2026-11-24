# SPDX-License-Identifier: AGPL-3.0-or-later
"""The approval frontier; the human-in-the-loop gate, by flow not by global knob.

This is the third piece of the brain's árbitro (§2.6, §2.9-(1) the approval queue,
§3-Fase 3 DoD). It sits AFTER the deterministic allowlist: once a task is *allowed*
to run, this layer decides whether a human still has to say "OK" first.

**HITL per flow, NOT global.** The decision is per-action, by risk; exactly the
lesson the brief draws from enterprise practice: "el umbral de confianza/aprobación
se define por tipo de acción según su riesgo, no uno global. Mover dinero o borrar
datos exige más (aprobación humana siempre); redactar un borrador exige menos."

``needs_approval`` combines three inputs (documented on the function). The autonomy
knob (``security.autonomy``) can LOWER this friction for repeatedly-approved human
actions, but never for the high-risk always-approve set, and it never raises trust.

Do-and-sleep (§2.1): a task that needs approval is *parked* in the approvals table
as pending; the machine reports ``pendiente_aprobacion`` and does NOT execute. A
later tick/check-in proceeds once a human resolves it from the queue.
"""

from dataclasses import dataclass
from typing import Optional, Union

from lhassa.config import Config
from lhassa.core import db
from lhassa.security import allowlist as al
from lhassa.security import autonomy
from lhassa.security.allowlist import AllowDecision
from lhassa.security.trust import TrustLevel


class ApprovalResolutionConflict(RuntimeError):
    """An approval was already resolved or changed concurrently."""


class ApprovalAuthorizationError(PermissionError):
    """The resolver is not authorized to approve this request."""


class UntrustedApproverError(ApprovalAuthorizationError):
    """A high-risk approval did not carry channel-authenticated identity."""


class SelfApprovalError(ApprovalAuthorizationError):
    """A high-risk action was approved by the actor that proposed it."""


@dataclass(frozen=True)
class TrustedApprover:
    """Identity asserted by an authenticated adapter, never by request payload.

    ``actor_id`` is the stable actor used for separation-of-duties checks and the
    audit trail. ``source`` records which trust boundary established it (for
    example ``api-token``, ``loopback``, ``telegram-owner`` or ``local-process``).
    Construct this at a channel boundary only; arbitrary user labels belong in
    ``by_actor`` and are deliberately ignored for high-risk authorization.
    """

    actor_id: str
    source: str

    def __post_init__(self) -> None:
        if not self.actor_id.strip() or not self.source.strip():
            raise ValueError("trusted approver actor_id and source must be non-empty")


_OWNER_ALIASES = frozenset({"api", "cli", "duenio", "owner", "telegram", "tui"})


def _principal(actor_id: str) -> str:
    """Canonicalize legacy channel actor labels to the one local owner.

    Older task producers identify the human by surface (``api``, ``telegram``,
    ``cli``). Approval identity is intentionally stable across those surfaces;
    otherwise the same person could propose over one channel and approve over
    another. Non-owner identities (org members, agents) remain distinct.
    """
    actor = (actor_id or "").strip()
    return "owner" if actor.casefold() in _OWNER_ALIASES else actor


def _always_approve() -> "set[str]":
    """The configured high-risk action set (always requires human approval)."""
    actions = Config.always_approve_actions
    return actions if isinstance(actions, set) else set(actions or [])


def _requires_separation_of_duties(payload: dict) -> bool:
    """Whether resolution needs authenticated, distinct approver identity.

    This mirrors every non-lowerable/per-action reason that can park dangerous
    work, rather than consulting only ``always_approve_actions``. Unknown action
    specs fail closed when the parked payload explicitly marks the request as
    requiring approval (new producers may stamp ``requires_approval``).
    """
    accion = str((payload or {}).get("accion") or "")
    if accion in _always_approve():
        return True
    spec = al.ALLOWLIST.get(accion)
    if spec is not None and spec.requires_approval:
        return True
    if (payload or {}).get("requires_separation_of_duties") is True:
        return True
    if (payload or {}).get("requires_approval") is True:
        return True
    params = (payload or {}).get("params")
    return _touches_protected_root(accion, params) or _attach_template_needs_gate(
        accion, params
    )


# File-perception actions whose ``path`` param is checked against the owner's
# protected roots (see input (d) in ``needs_approval``).
_PATH_ACTIONS = ("read_file", "list_dir")


def _touches_protected_root(accion: str, params) -> bool:
    """True when a file action's path resolves inside ``Config.protected_roots``.

    Fail-CLOSED: if the check itself errors we say True; when we can't tell
    whether a path is protected, the human decides.
    """
    if accion not in _PATH_ACTIONS:
        return False
    try:
        import os
        from pathlib import Path

        raw = (getattr(Config, "protected_roots", "") or "").strip()
        if not raw:
            return False
        if not isinstance(params, dict):
            return True
        target = str((params or {}).get("path", "") or "").strip()
        if not target:
            return True
        path = Path(os.path.expanduser(target)).resolve()
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            root = Path(os.path.expanduser(entry)).resolve()
            if path == root or root in path.parents:
                return True
        return False
    except Exception:
        return True


def _attach_template_needs_gate(accion: str, params) -> bool:
    """True (park) unless an ``attach_project_template`` task is PROVABLY read-only.

    Input (e): whether the standing-brains reflex's attach needs a human tap
    depends on the target org template's risk
    (``org.templates.template_is_read_only``). ``org`` is a SHELL and this module
    is an INTERFACE; it must never import a shell, even lazily (the architecture
    guard in ``tests/test_architecture.py`` walks every import regardless of
    nesting, so a function-body import would still trip it). So this does NOT
    import ``org.templates``: the classification is computed once, upstream, by
    the caller that mints the task (``actionpack/_standing.py``'s sense, where the
    ``org`` import IS legal) and stamped onto the task's own ``params`` as
    ``read_only``. This just reads that pre-computed flag.

    Fail-CLOSED like (d): missing/non-``True`` ⇒ gated. Only accion this applies
    to is ``attach_project_template``; any other accion returns ``False`` (no
    opinion; the other inputs decide).
    """
    if accion != "attach_project_template":
        return False
    if not isinstance(params, dict):
        return True
    return params.get("read_only") is not True


def needs_approval(
    decision_or_task: Union["AllowDecision", object],
    *,
    autonomy: Optional[bool] = None,
) -> bool:
    """Decide whether a human must approve before this action runs. Per-action.

    Inputs combined (HITL per flow; the threshold lives PER ACTION, never global):

    (a) **The allowlist's per-action approval flag.** ``ActionSpec.requires_approval``
        for the action (or ``AllowDecision.requires_approval`` when a decision is
        passed); the policy table says, action by action, whether HITL applies.
    (b) **Origin trust below the action's threshold.** When a ``Task`` is given, an
        origin whose ``TrustLevel`` is below the action's ``min_trust``: and ALWAYS
        when the origin is ``MUNDO`` (world-derived content is never trusted to act
        on its own); needs a human OK. ("una tarea origen `mundo`... espera tu OK".)
    (c) **The high-risk always-approve set.** Actions in
        ``Config.always_approve_actions`` (e.g. move_money / delete_data) ALWAYS need
        approval, regardless of everything else; including the autonomy knob.
    (d) **A protected-path file read** (see ``_touches_protected_root``); per
        access, never lowered by autonomy.
    (e) **A standing-brains attach whose template isn't provably read-only** (see
        ``_attach_template_needs_gate``); per attach, never lowered by autonomy.

    ``decision_or_task`` may be a ``Task`` (the rich case; all three inputs apply)
    or an ``AllowDecision`` (only input (a) is knowable). ``autonomy`` is the knob's
    verdict for this (accion, actor): when ``True`` it LOWERS friction and this
    returns ``False``: UNLESS the action is high-risk (c), which can never be
    lowered. The autonomy knob does not (and cannot) change trust here.
    """
    if isinstance(decision_or_task, AllowDecision):
        requires = decision_or_task.requires_approval
        high_risk = False
        trust_below = False
        is_mundo = False
    else:
        task = decision_or_task
        accion = task.accion
        spec = al.ALLOWLIST.get(accion)
        requires = spec.requires_approval if spec else False
        min_trust = spec.min_trust if spec is not None else TrustLevel.HUMANO
        trust = task.origen.trust
        trust_below = trust < min_trust
        is_mundo = trust <= TrustLevel.MUNDO
        high_risk = accion in _always_approve()

    # (d) Protected-path reads: a file action whose path resolves inside
    # ``Config.protected_roots`` ALWAYS asks the owner; per access, and (like
    # high-risk) never lowered by the autonomy knob: the tap IS the verification.
    if not isinstance(decision_or_task, AllowDecision) and _touches_protected_root(
        decision_or_task.accion, getattr(decision_or_task, "params", None)
    ):
        return True

    # (e) A standing-brains attach whose target template is not provably read-only
    # ALWAYS asks the owner; same never-lowered-by-autonomy standing as (d).
    if not isinstance(decision_or_task, AllowDecision) and _attach_template_needs_gate(
        decision_or_task.accion, getattr(decision_or_task, "params", None)
    ):
        return True

    # (c) High-risk ALWAYS needs approval; never lowered by autonomy or history.
    if high_risk:
        return True

    # The autonomy knob lowers friction for already-human-approved actions.
    if autonomy is True:
        return False

    return bool(requires or trust_below or is_mundo)


async def authorize_execution(task) -> tuple[bool, str]:
    """Require durable approval evidence at the final execution frontier.

    Signing proves task integrity, not that HITL was satisfied. Execution is
    authorized only when earned autonomy currently removes the gate, or an
    approved ``mint_task`` row names this exact task. Lookup errors deny.
    """
    try:
        auto = await autonomy.should_auto_approve(
            task.instance_id, task.accion, task.origen.actor_id
        )
        if not needs_approval(task, autonomy=auto):
            return True, "approval not required"
        if await db.has_approved_task_approval(task.instance_id, task.id):
            return True, "durable human approval found"
        return False, "required human approval evidence is missing"
    except Exception as exc:
        return False, f"approval evidence unavailable: {exc}"


async def request_approval(
    instance_id: str,
    *,
    tipo: str,
    payload: dict,
    approval_id: Optional[str] = None,
) -> str:
    """Park an action in the approvals queue (pending) and return its id.

    Do-and-sleep: the task waits as pending; the machine reports
    ``pendiente_aprobacion`` and never executes. ``payload`` should carry enough to
    both show the human what they are approving AND feed the autonomy knob on
    resolution; at minimum ``accion``, ``actor_id`` and ``origen_trust``.
    """
    # Snapshot the security classification into the durable row. A later config
    # or allowlist edit must never downgrade a request that was parked as
    # separation-of-duties work.
    payload = dict(payload or {})
    if _requires_separation_of_duties(payload):
        payload["requires_separation_of_duties"] = True
    if approval_id:
        existing = await db.get_approval(instance_id, approval_id)
        if existing is not None:
            return approval_id
    try:
        approval_id_out = await db.add_approval(
            instance_id, tipo, payload, approval_id=approval_id
        )
        try:
            await _stamp_english_brief(instance_id, approval_id_out, tipo, payload)
        except Exception:
            pass  # brief enrichment must never break the approval itself
        return approval_id_out
    except Exception:
        # A durable flow may retry after parking the request but before saving its
        # checkpoint.  The deterministic id turns that replay into a read.
        if approval_id and await db.get_approval(instance_id, approval_id) is not None:
            return approval_id
        raise


async def resolve(
    instance_id: str,
    approval_id: str,
    *,
    approved: bool,
    by_actor: str = "duenio",
    approver: Optional[TrustedApprover] = None,
) -> Optional[dict]:
    """Resolve a pending approval and feed the decision to the autonomy knob.

    Marks the approval ``aprobada`` / ``rechazada`` in the queue, then records the
    decision against the parked action's ``(accion, actor_id)`` so a repeatedly
    approved human action earns auto-approval and a denial resets its streak.
    ``by_actor`` is a backwards-compatible audit label for ordinary approvals.
    For high-risk approvals it is NEVER an authorization claim: callers must pass
    a ``TrustedApprover`` established by their authenticated channel.  When one is
    supplied, its actor id is authoritative for both policy and audit.
    """
    ap = await db.get_approval(instance_id, approval_id)
    if not ap:
        raise KeyError(approval_id)
    payload = ap.get("payload") or {}
    resolved_by = approver.actor_id.strip() if approver is not None else by_actor

    # Proposer ≠ approver, at the GENERAL seam. org/coordinator.py already
    # enforces this for its own delegate flow, but the generic tipo="task"
    # approvals; the ones carrying always_approve_actions like run_command;
    # had no check: any caller reaching the endpoint could self-approve by
    # naming any by_actor. High-risk only; routine approvals stay one-tap.
    if approved:
        accion = str(payload.get("accion") or "")
        proposer = str(payload.get("actor_id") or "")
        high_risk = _requires_separation_of_duties(payload)
        if high_risk and approver is None:
            raise UntrustedApproverError(
                f"high-risk {accion!r} approval requires identity from an "
                "authenticated channel"
            )
        if high_risk and not proposer:
            raise UntrustedApproverError(
                f"high-risk {accion!r} approval has no authenticated proposer identity"
            )
        if high_risk and proposer and _principal(proposer) == _principal(resolved_by):
            raise SelfApprovalError(
                f"self-approval refused: {resolved_by!r} proposed this "
                f"high-risk {accion!r} approval and cannot also resolve it"
            )
    result = None
    if ap.get("tipo") == "flow_gate" and payload.get("run_id"):
        # Lazy import keeps security independent from the flow engine at import
        # time. The durable service commits approval + run cursor atomically and
        # resumes approved work for API, CLI, and chat surfaces alike.
        from lhassa.flows.durable import RuntimeConflict, resolve_flow_gate

        try:
            result = await resolve_flow_gate(
                instance_id,
                approval_id,
                approved=approved,
                by_actor=resolved_by,
            )
        except RuntimeConflict as exc:
            # One exception contract for every surface: an already-resolved or
            # inconsistent gate surfaces exactly like the plain-CAS branch below.
            raise ApprovalResolutionConflict(str(exc)) from exc
        from lhassa.flows.projection import project_flow_run

        await project_flow_run(
            result["run"],
            trigger=result.get("trigger") or "manual",
            instance_id=instance_id,
        )
    else:
        estado = "aprobada" if approved else "rechazada"
        if not await db.resolve_approval(instance_id, approval_id, estado):
            raise ApprovalResolutionConflict(
                f"approval {approval_id} is already {ap.get('estado') or 'resolved'}"
            )
        # RELEASE the parked task the approval was minted for: an approve flips
        # it back to 'pendiente' (the next heartbeat (or run_task_now) picks
        # it up); a deny closes it as 'rechazada'. Without this the tap resolved
        # the QUEUE row but the task itself waited forever. Best-effort: a
        # release failure must not undo an already-recorded human decision.
        if ap.get("tipo") == "retire_capability":
            await _record_retirement_decision(instance_id, payload, approved)
        task_id = payload.get("task_id")
        if task_id and payload.get("source") == "mint_task":
            import contextlib

            with contextlib.suppress(Exception):
                await db.set_task_state(
                    instance_id, task_id, "pendiente" if approved else "rechazada"
                )

    accion = payload.get("accion") or ap.get("tipo")
    if not accion:
        return result
    await autonomy.record_decision(
        instance_id,
        accion,
        payload.get("actor_id"),
        approved=approved,
        origen_trust=payload.get("origen_trust"),
    )
    return result


async def _record_retirement_decision(instance_id: str, payload: dict, approved: bool) -> None:
    """What a human answer to a ``retire_capability`` approval MEANS.

    Approve: the capability joins ``utility:retired_candidates``; NOTHING is
    removed or disabled (a registered action lives in code). Reject: it is
    pinned (``utility:pinned:<cap>``) so the tribunal never asks again. Only
    :func:`resolve` (a human) gets here: the queue closures
    (``rejected-by-timeout/cap/revived/gone``) go through ``db.close_approval``
    and deliberately leave no pin. Best-effort: a failure must not undo the
    recorded decision.
    """
    cap = str((payload or {}).get("capability") or "")
    if not cap:
        return
    import contextlib

    from lhassa.agents import utility as _utility

    with contextlib.suppress(Exception):
        if approved:
            await _utility.add_retired_candidate(instance_id, cap)
        else:
            await _utility.pin_capability(instance_id, cap)


async def pending(instance_id: str) -> list:
    """The approval queue: pending approvals for ``instance_id`` (oldest first)."""
    return await db.load_pending_approvals(instance_id)


def describe(row: dict) -> dict:
    """A human-readable ``{title, detail}`` for one approval row; deterministic, $0.

    Every surface that shows the queue (the Telegram ``/approvals`` command, the
    dashboard panels, the digest) renders through this ONE helper, so "what am I
    approving?" reads the same everywhere. It understands the payload shape each
    ``request_approval`` caller writes (``mint_action``, ``mint_script``,
    ``mint_memory``, ``mint_schedule``, ``seed_cache``: the self-improvement
    consciousness's :func:`lhassa.agents.repetition.recommend_intervention`
    kinds; plus ``retire_capability`` (the utility tribunal),
    ``tool_graduation``, ``flow_gate``, ``org_task``, dispatch/
    remote tasks) and degrades to
    ``tipo``+``accion`` for anything unknown. Never raises.
    """
    tipo = str((row or {}).get("tipo") or "?")
    payload = (row or {}).get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    accion = str(payload.get("accion") or "")
    try:
        if tipo == "mint_action":
            cand = payload.get("candidate_accion") or payload.get("pattern") or "?"
            bits = []
            if payload.get("despertar_por"):
                bits.append(f"trigger: {payload['despertar_por']}")
            if payload.get("count"):
                bits.append(f"repeated ×{payload['count']}")
            if payload.get("total_cost_usd") is not None:
                bits.append(f"${float(payload['total_cost_usd']):.2f} spent on it so far")
            bits.append("approving mints a $0 deterministic action")
            return {
                "title": f"Make '{cand}' automatic",
                "detail": " · ".join(bits),
            }
        if tipo == "mint_script":
            chain = payload.get("chain") or []
            label = " → ".join(str(c) for c in chain) if chain else "?"
            bits = []
            if payload.get("count"):
                bits.append(f"repeated ×{payload['count']}")
            if payload.get("days_seen"):
                bits.append(f"on {payload['days_seen']} distinct day(s)")
            bits.append("approving only records the draft; no script is created or run")
            return {
                "title": f"Collapse the {label} chain into one script",
                "detail": " · ".join(bits),
            }
        if tipo == "mint_memory":
            subject = payload.get("despertar_por") or payload.get("pattern") or "?"
            bits = []
            if payload.get("count"):
                bits.append(f"asked ×{payload['count']}")
            if payload.get("days_seen"):
                bits.append(f"on {payload['days_seen']} distinct day(s)")
            if payload.get("total_cost_usd") is not None:
                bits.append(f"~${float(payload['total_cost_usd']):.2f} spent so far")
            bits.append("approving only records the pattern; nothing is stored")
            return {
                "title": f"Remember the answer to '{subject}' so I stop re-deriving it",
                "detail": " · ".join(bits),
            }
        if tipo == "mint_schedule":
            subject = payload.get("despertar_por") or payload.get("pattern") or "?"
            bits = []
            interval = payload.get("interval_days")
            if interval is not None:
                bits.append(f"a ~{interval}-day cadence")
            if payload.get("count"):
                bits.append(f"seen ×{payload['count']}")
            bits.append("approving schedules a $0 recurring action")
            return {
                "title": f"Answer '{subject}' on its cadence automatically",
                "detail": " · ".join(bits),
            }
        if tipo == "seed_cache":
            subject = payload.get("question") or payload.get("despertar_por") or "?"
            bits = []
            if payload.get("count"):
                bits.append(f"already served from cache ×{payload['count']}")
            bits.append("already $0; nothing further to mint")
            return {
                "title": f"'{subject}' is already answered from the cache",
                "detail": " · ".join(bits),
            }
        if tipo == "mint_route":
            capability = payload.get("capability") or payload.get("pattern") or "?"
            bits = []
            if payload.get("count"):
                bits.append(f"seen ×{payload['count']} going to a think/research")
            if payload.get("total_cost_usd") is not None:
                bits.append(f"${float(payload['total_cost_usd']):.2f} spent")
            bits.append("approving handles them free")
            return {
                "title": f"Route {capability} questions to the $0 handler",
                "detail": " · ".join(bits),
            }
        if tipo == "retire_capability":
            cap = payload.get("capability") or "?"
            state = str(payload.get("state") or "")
            bits = []
            owner_uses = int(payload.get("owner_uses") or 0)
            self_uses = int(payload.get("self_uses") or 0)
            days = payload.get("days_since_owner_use")
            if state == "unused":
                bits.append("never used, by anyone")
            else:
                if owner_uses:
                    bits.append(f"you used it ×{owner_uses}")
                if days is not None:
                    try:
                        bits.append(f"last owner use {float(days):.0f}d ago")
                    except (TypeError, ValueError):
                        pass
                elif not owner_uses:
                    bits.append("no owner use on record")
                if self_uses:
                    bits.append(f"Lhassa ran it ×{self_uses} on its own")
            bits.append(
                "approving records it as a retirement candidate; nothing is "
                "removed · rejecting pins it"
            )
            return {
                "title": f"Retire capability '{cap}'",
                "detail": " · ".join(bits),
            }
        if tipo == "tool_graduation":
            tool = payload.get("tool") or "?"
            detail = "passed the gates in quarantine; approving activates it"
            if payload.get("branch"):
                detail += f" (branch {payload['branch']})"
            return {"title": f"Graduate self-written tool '{tool}'", "detail": detail}
        if tipo == "code_run":
            task = str(payload.get("task") or "?")
            preview = task if len(task) <= 60 else task[:57] + "…"
            cli = payload.get("cli") or "?"
            cwd = payload.get("cwd") or "?"
            return {
                "title": f"Run a coding agent: {preview}",
                "detail": f"{cli} · {cwd} · write access to your files; approving launches it",
            }
        if tipo == "product_change_experiment":
            project = payload.get("project") or payload.get("project_id") or "?"
            objective = str(payload.get("objective") or "?")
            return {
                "title": f"Experiment with a researched change in '{project}'",
                "detail": (
                    f"{objective[:100]} · external worktree only · approving does "
                    "not merge anything"
                ),
            }
        if tipo == "product_change_integration":
            project = payload.get("project") or payload.get("project_id") or "?"
            objective = str(payload.get("objective") or "?")
            isolation = (payload.get("verification") or {}).get("isolation") or "?"
            return {
                "title": f"Integrate verified change into '{project}'",
                "detail": (
                    f"{objective[:100]} · gate passed in {isolation} · this approval "
                    "is distinct from permission to experiment"
                ),
            }
        if tipo == "flow_gate":
            node = payload.get("node") or "?"
            return {
                "title": f"Work-flow paused at gate '{node}'",
                "detail": "the flow waits at this step until you approve it",
            }
        if tipo == "org_task":
            dept = payload.get("department") or "?"
            bits = []
            if payload.get("goal"):
                bits.append(f"goal: {str(payload['goal'])[:100]}")
            if payload.get("actor_id"):
                bits.append(f"asked by {payload['actor_id']}")
            return {"title": f"Org task for '{dept}'", "detail": " · ".join(bits) or tipo}
        # Dispatch tasks (tipo="task") and remote tasks (tipo=<accion>) share
        # objetivo / actor_id / origen_trust.
        bits = []
        if payload.get("objetivo"):
            bits.append(f"goal: {str(payload['objetivo'])[:100]}")
        # A protected-root read parks here: the owner must see WHICH path
        # they are approving, never a bare action name.
        task_params = payload.get("params")
        if isinstance(task_params, dict) and task_params.get("path"):
            bits.append(f"path: {str(task_params['path'])[:120]}")
        if payload.get("actor_id"):
            bits.append(f"asked by {payload['actor_id']}")
        if payload.get("origen_trust") is not None:
            bits.append(f"origin trust: {payload['origen_trust']}")
        title = f"Run '{accion}'" if accion else f"Approval: {tipo}"
        detail = " · ".join(bits) or str(payload.get("title") or "")
        return {"title": title, "detail": detail}
    except Exception:  # a malformed payload must never break a queue render
        return {"title": tipo + (f": {accion}" if accion else ""), "detail": ""}

def deterministic_brief(tipo: str, payload: dict) -> Optional[dict]:
    """A rule-based ``{summary, recommendation}`` for self-proposals; no LLM.

    ``retire_capability`` and the ``mint_*`` kinds are decided from numbers the
    payload already carries (uses, dates, counts), so a model that only saw the
    title (and wrote most of the old briefs) adds nothing but invention.
    Returns ``None`` for any other type.
    """
    p = payload or {}
    if tipo == "retire_capability":
        cap = p.get("capability") or "?"
        owner = int(p.get("owner_uses") or 0)
        selfu = int(p.get("self_uses") or 0)
        total = int(p.get("total_uses") or 0)
        days = p.get("days_since_owner_use")
        last = p.get("last_use_at")
        state = str(p.get("state") or "")
        summary = (
            f"Record '{cap}' as a retirement candidate. Uses: {total} total, "
            f"{owner} by you, {selfu} by Lhassa itself"
        )
        if days is not None:
            summary += f"; last owner use {float(days):.0f} days ago"
        if last:
            summary += f"; last use of any kind {str(last)[:10]}"
        summary += ". Approving removes nothing."
        if state == "unused" or total == 0:
            rec = "Approve: no use by anyone, ever."
        elif owner > 0 and days is not None and float(days) <= 90:
            rec = f"Reject: you used it {float(days):.0f} days ago; rejecting keeps it."
        elif owner > 0:
            rec = f"Approve: no owner use in {float(days or 0):.0f} days."
        else:
            rec = f"Approve: only Lhassa used it ({selfu} times); you never did."
        return {"summary": summary, "recommendation": rec}
    if tipo in ("mint_script", "mint_memory"):
        count = int(p.get("count") or 0)
        days_seen = int(p.get("days_seen") or 0)
        if tipo == "mint_script":
            label = " > ".join(str(c) for c in (p.get("chain") or [])) or "?"
            summary = (
                f"Record a draft script for the chain {label}, seen {count} times "
                f"on {days_seen or '?'} distinct day(s). Nothing is created or run."
            )
            good = count >= 5 and days_seen >= 2
            rec = (
                f"Approve: repeated {count} times over {days_seen} days."
                if good
                else f"Reject: only {count} repeats over {days_seen or '?'} day(s)."
            )
        else:
            subject = p.get("despertar_por") or p.get("pattern") or "?"
            has_answer = bool(p.get("answer") or p.get("fact"))
            summary = (
                f"Record the pattern '{subject}', asked {count} times on "
                f"{days_seen or '?'} distinct day(s). Nothing is stored."
            )
            rec = (
                f"Approve: repeated over {days_seen} days with an answer attached."
                if has_answer and days_seen >= 2
                else "Reject: no stable answer or too few days."
            )
        return {"summary": summary, "recommendation": rec}
    if tipo.startswith("mint_"):
        count = int(p.get("count") or 0)
        return {
            "summary": f"Self-proposal {tipo}, pattern repeated {count} times.",
            "recommendation": (
                "Approve: repeated often enough." if count >= 5
                else "Reject: too few repeats."
            ),
        }
    return None


async def store_brief(approval_id: str, data: dict) -> None:
    """Persist ``data`` as the approval's brief (adds the column if needed)."""
    import json

    from lhassa._vendor import aiosqlite
    from lhassa.core.db._conn import _pkg
    async with aiosqlite.connect(_pkg().DB_PATH) as db:
        # The column is created lazily (the API's brief route did it first);
        # a brief stamped before any dashboard visit must not be lost.
        async with db.execute("PRAGMA table_info(approvals)") as cur:
            if "brief" not in [r[1] for r in await cur.fetchall()]:
                await db.execute("ALTER TABLE approvals ADD COLUMN brief TEXT")
        await db.execute(
            "UPDATE approvals SET brief = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), approval_id),
        )
        await db.commit()


async def _stamp_english_brief(instance_id: str, approval_id: str, tipo: str, payload: dict) -> None:
    """Generate and store the English decision brief at request time.

    Self-proposals (``retire_capability``, ``mint_*``) get a deterministic,
    rule-based brief (:func:`deterministic_brief`); everything else asks the
    cheap model.
    """
    import json
    det = deterministic_brief(tipo, payload)
    if det is not None:
        await store_brief(approval_id, det)
        return
    from lhassa.config import Config
    from lhassa.llm.gateway import gateway
    item = {
        "tipo": tipo,
        "title": str((payload or {}).get("title") or ""),
        "rationale": str((payload or {}).get("rationale") or ""),
        "detail": str((payload or {}).get("detail") or ""),
        "accion": str((payload or {}).get("accion") or ""),
    }
    prompt = (
        "You are Lhassa, the owner's personal assistant. An approval request was "
        "created. Write a short English summary (1-2 sentences) of what is being "
        "asked and a clear recommendation: approve or reject, with the reason. "
        "Never use em dashes; use ':' or ',' instead. "
        'Return ONLY JSON: {"summary": "...", "recommendation": "Approve/Reject ..."}.\n'
        + json.dumps(item, ensure_ascii=False)
    )
    result = await gateway.complete(
        [{"role": "user", "content": prompt}],
        model=Config().model_cheap,
        max_tokens=400,
        temperature=0.2,
    )
    raw = (result.text or "").strip()
    import re as _re
    raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=_re.S)
    m = _re.search(r"\{[\s\S]*\}", raw)
    data = json.loads(m.group(0) if m else raw)
    if not isinstance(data, dict):
        return
    # The owner's rule: no em dashes anywhere, including model-written text.
    data = {k: _re.sub(r"\s*[\u2014\u2013]\s*", ": ", str(v)).strip() for k, v in data.items()}
    await store_brief(approval_id, data)



# ── queue hygiene: self-proposals nobody answered ────────────────────────────

# Approvals Lhassa raises about ITSELF (retire a capability, mint a script or a
# memory). Nothing waits on them, so leaving them unanswered costs nothing but
# queue noise, and closing them is safe. Anything else (a parked task, a flow
# gate, a high-risk action) has something waiting on it and is never expired.
SELF_PROPOSAL_TIPOS = ("retire_capability", "mint_script", "mint_memory", "mint_schedule", "mint_action")


def _expirable(row: dict) -> bool:
    if row.get("tipo") not in SELF_PROPOSAL_TIPOS:
        return False
    payload = row.get("payload") or {}
    return not (payload.get("task_id") or payload.get("run_id"))


def _age_days(created: str, now) -> Optional[float]:
    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(created)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).total_seconds() / 86400.0


async def expire_stale(
    instance_id: str,
    *,
    days: Optional[int] = None,
    cap: Optional[int] = None,
    now=None,
    dry_run: bool = False,
) -> dict:
    """Close unanswered self-proposals: too old, or beyond the per-type cap.

    * older than ``days`` (default ``Config.approval_expiry_days``, 0 disables):
      closed ``rechazada`` with ``resolution: rejected-by-timeout``;
    * more than ``cap`` still pending of one type (default
      ``Config.approval_pending_cap``, 0 disables): the OLDEST beyond the cap are
      closed with ``resolution: rejected-by-cap``.

    A closure is NOT a human decision: it does not touch the autonomy streaks, the
    feedback signals or any parked task (only task-free self-proposals qualify, see
    :func:`_expirable`). Returns ``{"timeout": [...], "cap": [...], "kept": n}``
    with the closed ids; with ``dry_run`` nothing is written.
    """
    from datetime import datetime, timezone

    days = Config.approval_expiry_days if days is None else days
    cap = Config.approval_pending_cap if cap is None else cap
    now = now or datetime.now(timezone.utc)
    rows = [r for r in await db.load_pending_approvals(instance_id) if _expirable(r)]
    closed: dict[str, list[str]] = {"timeout": [], "cap": []}

    async def _close(row: dict, why: str) -> None:
        if dry_run or await db.close_approval(instance_id, row["id"], f"rejected-by-{why}"):
            closed[why].append(row["id"])

    survivors: list[dict] = []
    for row in rows:  # oldest first
        age = _age_days(row.get("creado_en") or "", now)
        if days > 0 and age is not None and age > days:
            await _close(row, "timeout")
        else:
            survivors.append(row)
    if cap > 0:
        by_tipo: dict[str, list[dict]] = {}
        for row in survivors:
            by_tipo.setdefault(row["tipo"], []).append(row)
        for tipo_rows in by_tipo.values():
            for row in tipo_rows[: max(0, len(tipo_rows) - cap)]:
                await _close(row, "cap")
    kept = len(rows) - len(closed["timeout"]) - len(closed["cap"])
    return {**closed, "kept": kept}


async def pending_count(instance_id: str, tipo: str) -> int:
    """How many approvals of ``tipo`` are pending (the creation-side cap reads this)."""
    return sum(1 for r in await db.load_pending_approvals(instance_id) if r.get("tipo") == tipo)
