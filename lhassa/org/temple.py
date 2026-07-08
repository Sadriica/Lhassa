"""The templo internal-delegation loop — Lhassa's own inner workshop.

:mod:`lhassa.org.delegate` is the DOWN-hop exemplar for a *flow*; :mod:`lhassa.org.deliver`
is the UP-hop mirror for a *work product*. This module wires the SAME two seams
together end to end for Lhassa's INTERNAL functions — docs, internal creation
(tools/skills/flows authoring, the consciousness proposals), synchronization
(repo/calendar/memory), and self-maintenance (curate/review/detect_repetition/
distill) — through the ``templo`` department (Lhassa's inner temple, distinct
from ``interns``/``personal``, see :mod:`lhassa.seeds.templo`).

The whole loop, one call:

  1. **DOWN** — resolve the command path from the root admin (Lhassa) down to
     ``templo`` (:func:`lhassa.org.delegate.command_path`) and log ONE
     deterministic, $0, LLM-free ``org-delegate`` Episode narrating the handoff
     (mirrors :func:`lhassa.org.delegate._log_delegation` exactly). A broken
     topology degrades to a direct run — governance must NEVER block the work.
  2. **EXECUTE** — templo actually DOES the work: the deterministic action runs
     via :func:`lhassa.core.actions.run`, and its own execution Episode is
     attributed ``dominio="templo"`` — the temple's own hop in the log.
  3. **UP** — the result travels back to the owner via
     :func:`lhassa.org.deliver.deliver` (``origin_department="templo"``), which
     itself writes one Episode per brain on the chain before the final send —
     Lhassa is the sender of record.

Every step is best-effort except the actual execution: a delegation-episode
failure or a deliver failure is logged and swallowed, never blocks the action
or the caller. :func:`delegate_internal` itself NEVER raises.

For JUDGMENT internal work (e.g. authoring a doc from a free-form instruction,
not a fixed deterministic action), the caller should instead go through
``org_run`` with ``params={"department": "templo"}`` (see
:mod:`lhassa.actionpack._org`) — the real coordinator path, gated behind
``LHASSA_ORG_LIVE`` like every other judgment delegation. :func:`delegate_internal`
is the DETERMINISTIC core of the loop; judgment work rides the existing
``org_run`` seam with the department hint pointed at ``templo``.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import actions as _actions
from lhassa.core import db
from lhassa.core.episode import Episode
from lhassa.org import delegate

#: The templo department id (matches lhassa.seeds.templo.TEMPLO_ID).
TEMPLO_DEPARTMENT = "templo"

#: The trigger label for templo's OWN execution episode — a distinct, greppable
#: marker alongside ``org-delegate`` (the down hop) and ``org-deliver`` (the up hop).
DESPERTAR_EXECUTE = "templo-execute"


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    return Config()


async def _log_down_hop(
    action: str, instruction: str, chain: list[str], *, config: "Optional[Config]"
) -> None:
    """Log the DOWN hop — mirrors :func:`lhassa.org.delegate._log_delegation`.

    One deterministic, $0, LLM-free ``org-delegate`` Episode narrating "Lhassa
    delegates <action> down to templo" and the root→…→templo path.
    """
    instance_id = getattr(config, "instance_id", None) or Config.instance_id
    arrow = "→".join(chain) if chain else TEMPLO_DEPARTMENT
    decision = f"lhassa delegates internal work '{action}' down to templo"
    if instruction:
        decision += f": {instruction}"
    await db.init_db()
    ep = Episode.begin(despertar_por=delegate.DESPERTAR_DELEGATE, instance_id=instance_id)
    ep.finish(
        decision=decision,
        accion=f"delegate:{arrow}",
        resultado=" → ".join(chain) if chain else TEMPLO_DEPARTMENT,
        costo_usd=0.0,
    )
    await ep.append()


async def _execute_as_templo(
    action: str, params: dict, *, config: "Optional[Config]"
) -> str:
    """Run ``action`` via the deterministic ACTIONS registry, attributed to templo.

    Logs its OWN Episode with ``dominio="templo"`` — the temple's hop in the log,
    distinct from the down-delegation and up-delivery hops. Never raises: any
    failure is captured in the returned result string (mirrors
    :func:`lhassa.core.actions.run`'s own never-raise contract).
    """
    instance_id = getattr(config, "instance_id", None) or Config.instance_id
    await db.init_db()
    ep = Episode.begin(despertar_por=DESPERTAR_EXECUTE, instance_id=instance_id)
    ep.dominio = TEMPLO_DEPARTMENT
    try:
        result = await _actions.run(action, dict(params or {}), ep)
    except Exception as exc:  # actions.run already never raises, but be sure
        result = f"templo: {action} errored: {exc}"
    ep.finish(
        decision=f"templo executes {action}",
        accion=action,
        resultado=str(result)[:500],
        costo_usd=0.0,
    )
    try:
        await ep.append()
    except Exception as exc:  # observability must never block the work
        logger.debug("org.temple: execution episode write failed for {}: {}", action, exc)
    return result


async def delegate_internal(
    action: str,
    params: dict,
    *,
    config: "Optional[Config]" = None,
    instruction: str = "",
) -> dict:
    """The whole down-execute-up loop for one piece of internal work.

    1. Resolve the command path Lhassa → … → templo and log the DOWN
       ``org-delegate`` Episode (best-effort — a broken topology degrades to a
       direct run, never blocking the work).
    2. EXECUTE ``action`` as the templo brain via the deterministic ACTIONS
       registry (:func:`lhassa.core.actions.run`); its Episode is attributed
       ``dominio="templo"``.
    3. Deliver the result UP via :func:`lhassa.org.deliver.deliver`
       (``origin_department="templo"``) — the per-brain up-hops + the final
       send, so the result returns to Lhassa, the sender of record.

    Returns ``{"ok": bool, "action": str, "result": str, "delivered": bool}``.
    NEVER raises.
    """
    try:
        cfg = _resolve_config(config)
        action = str(action or "").strip()
        if not action:
            return {"ok": False, "action": action, "result": "no action given", "delivered": False}

        # ── 1. DOWN — delegate ────────────────────────────────────────────────
        chain: list[str] = []
        try:
            from lhassa.org import topology as topo_mod

            topology = topo_mod.load_topology(cfg)
            chain = delegate.command_path(topology, TEMPLO_DEPARTMENT)
            await _log_down_hop(action, instruction, chain, config=cfg)
        except Exception as exc:  # governance must never block the work
            logger.debug(
                "org.temple: delegation degraded to direct run ({}): {}", action, exc
            )

        # ── 2. EXECUTE — templo does the work ─────────────────────────────────
        result = await _execute_as_templo(action, params, config=cfg)

        # ── 3. UP — deliver the result to the owner (Lhassa is sender of record) ─
        delivered = False
        try:
            from lhassa.org import deliver as deliver_mod

            summary = f"templo completed {action}: {str(result)[:300]}"
            delivered, _artifact_id = await deliver_mod.deliver(
                text=summary,
                kind="internal",
                metadata={"action": action, "instruction": instruction},
                origin_department=TEMPLO_DEPARTMENT,
                config=cfg,
            )
        except Exception as exc:  # delivery audit is best-effort; the work is done
            logger.debug("org.temple: deliver failed for {}: {}", action, exc)

        return {"ok": True, "action": action, "result": result, "delivered": delivered}
    except Exception as exc:  # the whole loop must never raise
        logger.warning("org.temple: delegate_internal failed for {}: {}", action, exc)
        return {"ok": False, "action": action, "result": f"error: {exc}", "delivered": False}
