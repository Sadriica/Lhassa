"""The approval frontier — the human-in-the-loop gate, by flow not by global knob.

This is the third piece of the brain's árbitro (§2.6, §2.9-(1) the approval queue,
§3-Fase 3 DoD). It sits AFTER the deterministic allowlist: once a task is *allowed*
to run, this layer decides whether a human still has to say "OK" first.

**HITL per flow, NOT global.** The decision is per-action, by risk — exactly the
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

from typing import Optional, Union

from lhassa.config import Config
from lhassa.core import db
from lhassa.security import allowlist as al
from lhassa.security import autonomy
from lhassa.security.allowlist import AllowDecision
from lhassa.security.trust import TrustLevel


def _always_approve() -> "set[str]":
    """The configured high-risk action set (always requires human approval)."""
    actions = Config.always_approve_actions
    return actions if isinstance(actions, set) else set(actions or [])


def needs_approval(
    decision_or_task: Union["AllowDecision", object],
    *,
    autonomy: Optional[bool] = None,
) -> bool:
    """Decide whether a human must approve before this action runs. Per-action.

    Inputs combined (HITL per flow — the threshold lives PER ACTION, never global):

    (a) **The allowlist's per-action approval flag.** ``ActionSpec.requires_approval``
        for the action (or ``AllowDecision.requires_approval`` when a decision is
        passed) — the policy table says, action by action, whether HITL applies.
    (b) **Origin trust below the action's threshold.** When a ``Task`` is given, an
        origin whose ``TrustLevel`` is below the action's ``min_trust`` — and ALWAYS
        when the origin is ``MUNDO`` (world-derived content is never trusted to act
        on its own) — needs a human OK. ("una tarea origen `mundo`... espera tu OK".)
    (c) **The high-risk always-approve set.** Actions in
        ``Config.always_approve_actions`` (e.g. move_money / delete_data) ALWAYS need
        approval, regardless of everything else — including the autonomy knob.

    ``decision_or_task`` may be a ``Task`` (the rich case — all three inputs apply)
    or an ``AllowDecision`` (only input (a) is knowable). ``autonomy`` is the knob's
    verdict for this (accion, actor): when ``True`` it LOWERS friction and this
    returns ``False`` — UNLESS the action is high-risk (c), which can never be
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

    # (c) High-risk ALWAYS needs approval — never lowered by autonomy or history.
    if high_risk:
        return True

    # The autonomy knob lowers friction for already-human-approved actions.
    if autonomy is True:
        return False

    return bool(requires or trust_below or is_mundo)


async def request_approval(instance_id: str, *, tipo: str, payload: dict) -> str:
    """Park an action in the approvals queue (pending) and return its id.

    Do-and-sleep: the task waits as pending; the machine reports
    ``pendiente_aprobacion`` and never executes. ``payload`` should carry enough to
    both show the human what they are approving AND feed the autonomy knob on
    resolution — at minimum ``accion``, ``actor_id`` and ``origen_trust``.
    """
    return await db.add_approval(instance_id, tipo, payload)


async def resolve(
    instance_id: str,
    approval_id: str,
    *,
    approved: bool,
    by_actor: str,
) -> None:
    """Resolve a pending approval and feed the decision to the autonomy knob.

    Marks the approval ``aprobada`` / ``rechazada`` in the queue, then records the
    decision against the parked action's ``(accion, actor_id)`` so a repeatedly
    approved human action earns auto-approval and a denial resets its streak.
    ``by_actor`` is the human who resolved it (recorded for the audit trail).
    """
    ap = await db.get_approval(instance_id, approval_id)
    estado = "aprobada" if approved else "rechazada"
    await db.resolve_approval(instance_id, approval_id, estado)

    if not ap:
        return
    payload = ap.get("payload") or {}
    accion = payload.get("accion") or ap.get("tipo")
    if not accion:
        return
    await autonomy.record_decision(
        instance_id,
        accion,
        payload.get("actor_id"),
        approved=approved,
        origen_trust=payload.get("origen_trust"),
    )


async def pending(instance_id: str) -> list:
    """The approval queue: pending approvals for ``instance_id`` (oldest first)."""
    return await db.load_pending_approvals(instance_id)


def describe(row: dict) -> dict:
    """A human-readable ``{title, detail}`` for one approval row — deterministic, $0.

    Every surface that shows the queue (the Telegram ``/approvals`` command, the
    dashboard panels, the digest) renders through this ONE helper, so "what am I
    approving?" reads the same everywhere. It understands the payload shape each
    ``request_approval`` caller writes (``mint_action``, ``tool_graduation``,
    ``flow_gate``, ``org_task``, dispatch/remote tasks) and degrades to
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
            bits.append("approving records the draft")
            bits.append("running it still crosses the approval gate")
            return {
                "title": f"Collapse the {label} chain into one script",
                "detail": " · ".join(bits),
            }
        if tipo == "tool_graduation":
            tool = payload.get("tool") or "?"
            detail = "passed the gates in quarantine — approving activates it"
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
                "detail": f"{cli} · {cwd} · write access to your files — approving launches it",
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
        if payload.get("actor_id"):
            bits.append(f"asked by {payload['actor_id']}")
        if payload.get("origen_trust") is not None:
            bits.append(f"origin trust: {payload['origen_trust']}")
        title = f"Run '{accion}'" if accion else f"Approval: {tipo}"
        detail = " · ".join(bits) or str(payload.get("title") or "")
        return {"title": title, "detail": detail}
    except Exception:  # a malformed payload must never break a queue render
        return {"title": tipo + (f": {accion}" if accion else ""), "detail": ""}
