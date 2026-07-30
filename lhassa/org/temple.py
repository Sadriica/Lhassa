"""The temple internal-delegation loop — Lhassa's own inner workshop.

:mod:`lhassa.org.delegate` is the DOWN-hop exemplar for a *flow*; :mod:`lhassa.org.deliver`
is the UP-hop mirror for a *work product*. This module wires the SAME two seams
together end to end for Lhassa's INTERNAL functions — docs, internal creation
(tools/skills/flows authoring, the consciousness proposals), synchronization
(repo/calendar/memory), and self-maintenance (curate/review/detect_repetition/
distill) — through the ``temple`` department (Lhassa's inner temple, distinct
from ``interns``/``personal``, see :mod:`lhassa.seeds.temple`).

**The monks.** ``temple`` has five leaf worker brains under it (created by
:mod:`lhassa.seeds.temple`), each OWNING a sub-domain: ``scribe`` (docs),
``artificer`` (internal creation — tools/skills/flows authoring), ``synchronizer``
(sync — repo/calendar/memory), ``guardian`` (self-maintenance — curate/review/
distill) and ``oracle`` (the consciousness — repetition detection, self-routing
proposals). :data:`ACTION_OWNERS` maps each of the five deterministic maintenance
actions to the monk that owns it; an unmapped action (or a topology missing the
resolved worker) falls back to ``temple`` itself — the department never fails to
have SOMEONE attributed.

The whole loop, one call:

  1. **DOWN** — resolve the owning WORKER for ``action`` (:func:`resolve_worker`),
     then the command path from the root admin (Lhassa) down to it
     (:func:`lhassa.org.delegate.command_path` — ``lhassa → temple → <worker>``
     when the worker exists in the topology, else ``lhassa → temple``) and log
     ONE deterministic, $0, LLM-free ``org-delegate`` Episode narrating the
     handoff (mirrors :func:`lhassa.org.delegate._log_delegation` exactly). A
     broken topology degrades to a direct run, attributed to ``temple`` —
     governance must NEVER block the work.
  2. **EXECUTE** — the resolved WORKER actually DOES the work: the deterministic
     action runs via :func:`lhassa.core.actions.run`, and its own execution
     Episode is attributed ``dominio=<worker>`` — e.g. the monk ``guardian``,
     not the department abstractly. This is the first instance of an org-wide
     pattern: **every tool reports to its brain** — the action's own outcome is
     the worker's Episode, which then reports UP through its department, exactly
     like a person handing finished work to their manager.
  3. **UP** — the result travels back to the owner via
     :func:`lhassa.org.deliver.deliver` (``origin_department=<worker>``), which
     itself writes one Episode per brain on the chain — worker → temple → Lhassa
     — before the final send. Lhassa is the sender of record.

Every step is best-effort except the actual execution: a delegation-episode
failure or a deliver failure is logged and swallowed, never blocks the action
or the caller. :func:`delegate_internal` itself NEVER raises.

For JUDGMENT internal work (e.g. authoring a doc from a free-form instruction,
not a fixed deterministic action), the caller should instead go through
``org_run`` with ``params={"department": "temple"}`` (see
:mod:`lhassa.actionpack._org`) — the real coordinator path, gated behind
``LHASSA_ORG_LIVE`` like every other judgment delegation. :func:`delegate_internal`
is the DETERMINISTIC core of the loop; judgment work rides the existing
``org_run`` seam with the department hint pointed at ``temple``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import actions as _actions
from lhassa.core import db
from lhassa.core.episode import Episode
from lhassa.org import delegate

if TYPE_CHECKING:
    from lhassa.org.topology import Topology

#: The temple department id (matches lhassa.seeds.temple.TEMPLE_ID).
TEMPLE_DEPARTMENT = "temple"

#: The temple's worker brains (the "monks") — leaf brains under ``temple``, each
#: owning a sub-domain of internal work. Ids are ASCII-safe on purpose (matched
#: 1:1 by lhassa.seeds.temple, the single source of truth for CREATING them).
SCRIBE = "scribe"  # docs — documentation upkeep
ARTIFICER = "artificer"  # internal creation — tools/skills/flows authoring
SYNCHRONIZER = "synchronizer"  # sync — repo/calendar/memory sync
GUARDIAN = "guardian"  # self-maintenance — curate/review/distill
ORACLE = "oracle"  # the consciousness — repetition detection, self-routing
# The scholar-monk. Deliberately a TIBETAN id (a geshe is the monk who earned
# the doctorate in philosophy) breaking the English-id convention — the one
# mythology touch this list carries, because the research method IS its role:
# how Lhassa writes, drafts, builds and focuses its research.
GESHE = "geshe"  # the research line — method, evidence-cited notes, focus

#: All five monks, in a stable order (docs, creation, sync, maintenance, mind).
TEMPLE_WORKERS: tuple[str, ...] = (SCRIBE, ARTIFICER, SYNCHRONIZER, GUARDIAN, ORACLE, GESHE)

#: The geshe's AIDES — the research support department under the scholar-monk
#: (seeded by ``seeds/research.py``). They mirror the corpus method's steps:
#: capture sources → analyze mechanics → verify claims → synthesize the doc.
#: The skeptic exists because the doer never judges — verification is a
#: separate brain by design.
GESHE_AIDES: tuple[str, ...] = ("collector", "analyst", "skeptic", "synthesist")

#: Which monk owns which internal action. Resolved BEFORE the down-hop in
#: :func:`resolve_worker`; an action absent here (or whose worker is missing
#: from the topology) falls back to ``temple`` itself — the department.
ACTION_OWNERS: dict[str, str] = {
    "distill_memory": GUARDIAN,
    # The knowledge graph is stored signal turned durable structure — the
    # guardian's domain, like distill_memory.
    "kg_sync": GUARDIAN,
    "kg_resolve": GUARDIAN,
    "review_tools": GUARDIAN,
    "curate_skills": GUARDIAN,
    "detect_repetition": ORACLE,
    # Research artifacts belong to the scholar-monk, not the docs scribe: the
    # geshe owns HOW Lhassa writes, drafts and focuses its research (the
    # method lives in research/METHOD.md and rides its research-method skill).
    "draft_research_note": GESHE,
    # Feedback→autonomy is the consciousness reading its own reception — the
    # oracle's domain, like repetition detection.
    "apply_feedback": ORACLE,
    "calendar_sync": SYNCHRONIZER,
    # Memory enrichment — bootstrapping durable facts (courses, commitments,
    # rhythm) from the calendar feed is the same domain as distill_memory
    # (turning a raw signal into durable grande facts), so it sits with
    # guardian rather than synchronizer (which owns the calendar SYNC/deliver
    # loop, not fact-writing) or oracle (pattern detection over episodes, not
    # calendar-derived world knowledge).
    "derive_world_facts": GUARDIAN,
    # Document perception — indexing the owner's knowledge roots is a sync
    # pass over an external-ish source (the filesystem), same domain as
    # calendar_sync, so it sits with synchronizer.
    "ingest_knowledge": SYNCHRONIZER,
}

#: The trigger label for the worker's OWN execution episode — a distinct, greppable
#: marker alongside ``org-delegate`` (the down hop) and ``org-deliver`` (the up hop).
DESPERTAR_EXECUTE = "temple-execute"


def resolve_worker(action: str, topology: "Topology") -> str:
    """The monk that OWNS ``action`` — deterministic, table-driven, $0.

    Looks up :data:`ACTION_OWNERS`; degrades to ``temple`` (the department
    itself) when the action is unmapped, or when the mapped worker does not
    (yet) exist in ``topology`` (e.g. an env whose seed hasn't been re-run).
    Never raises — any lookup failure degrades to ``temple`` too.
    """
    candidate = ACTION_OWNERS.get(action, TEMPLE_DEPARTMENT)
    try:
        return candidate if topology.has(candidate) else TEMPLE_DEPARTMENT
    except Exception:
        return TEMPLE_DEPARTMENT


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    return Config()


async def _log_down_hop(
    action: str, instruction: str, chain: list[str], worker: str, *, config: "Optional[Config]"
) -> None:
    """Log the DOWN hop — mirrors :func:`lhassa.org.delegate._log_delegation`.

    One deterministic, $0, LLM-free ``org-delegate`` Episode narrating "Lhassa
    delegates <action> down to <worker>" and the root→…→worker path.
    """
    instance_id = getattr(config, "instance_id", None) or Config.instance_id
    arrow = "→".join(chain) if chain else worker
    decision = f"lhassa delegates internal work '{action}' down to {worker}"
    if instruction:
        decision += f": {instruction}"
    await db.init_db()
    ep = Episode.begin(despertar_por=delegate.DESPERTAR_DELEGATE, instance_id=instance_id)
    ep.finish(
        decision=decision,
        accion=f"delegate:{arrow}",
        resultado=" → ".join(chain) if chain else worker,
        costo_usd=0.0,
    )
    await ep.append()


async def _execute_as_worker(
    action: str, params: dict, worker: str, *, config: "Optional[Config]"
) -> str:
    """Run ``action`` via the deterministic ACTIONS registry, attributed to ``worker``.

    Logs its OWN Episode with ``dominio=<worker>`` — the monk's hop in the log,
    distinct from the down-delegation and up-delivery hops. Never raises: any
    failure is captured in the returned result string (mirrors
    :func:`lhassa.core.actions.run`'s own never-raise contract).

    This is the "tool reports to its brain" exemplar: the action's own outcome
    IS the worker's Episode (``accion``=the tool, ``resultado``=its outcome),
    and — when a real monk (not the bare department) ran it — the ``resultado``
    also names that it reports up through ``temple``, so the trail reads
    "guardian ran curate_skills → 3 stale skills archived → reports ↑ temple"
    end to end from the tool's own log line.
    """
    instance_id = getattr(config, "instance_id", None) or Config.instance_id
    await db.init_db()
    ep = Episode.begin(despertar_por=DESPERTAR_EXECUTE, instance_id=instance_id)
    ep.dominio = worker
    try:
        result = await _actions.run(action, dict(params or {}), ep)
    except Exception as exc:  # actions.run already never raises, but be sure
        result = f"{worker}: {action} errored: {exc}"
    resultado = str(result)[:500]
    if worker != TEMPLE_DEPARTMENT:
        resultado = f"{resultado} · reports ↑ {TEMPLE_DEPARTMENT}"[:500]
    ep.finish(
        decision=f"{worker} executes {action}",
        accion=action,
        resultado=resultado,
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

    1. Resolve the owning WORKER for ``action`` (:func:`resolve_worker`), then
       the command path Lhassa → … → temple → <worker> and log the DOWN
       ``org-delegate`` Episode (best-effort — a broken topology degrades to a
       direct run attributed to temple, never blocking the work).
    2. EXECUTE ``action`` as the resolved WORKER via the deterministic ACTIONS
       registry (:func:`lhassa.core.actions.run`); its Episode is attributed
       ``dominio=<worker>`` — the monk that did the work, reporting to temple.
    3. Deliver the result UP via :func:`lhassa.org.deliver.deliver`
       (``origin_department=<worker>``) — the per-brain up-hops (worker →
       temple → Lhassa) + the final send, so the result returns to Lhassa, the
       sender of record.

    Returns ``{"ok": bool, "action": str, "result": str, "delivered": bool}``.
    NEVER raises.
    """
    try:
        cfg = _resolve_config(config)
        action = str(action or "").strip()
        if not action:
            return {"ok": False, "action": action, "result": "no action given", "delivered": False}

        # ── 1. DOWN — resolve the owning monk, then delegate ──────────────────
        chain: list[str] = []
        worker = TEMPLE_DEPARTMENT
        try:
            from lhassa.org import topology as topo_mod

            topology = topo_mod.load_topology(cfg)
            worker = resolve_worker(action, topology)
            chain = delegate.command_path(topology, worker)
            await _log_down_hop(action, instruction, chain, worker, config=cfg)
        except Exception as exc:  # governance must never block the work
            logger.debug(
                "org.temple: delegation degraded to direct run ({}): {}", action, exc
            )
            worker = TEMPLE_DEPARTMENT

        # ── 2. EXECUTE — the monk does the work ───────────────────────────────
        result = await _execute_as_worker(action, params, worker, config=cfg)

        # ── 3. UP — deliver the result to the owner (Lhassa is sender of record) ─
        delivered = False
        try:
            from lhassa.org import deliver as deliver_mod

            summary = f"{worker} completed {action}: {str(result)[:300]}"
            delivered, _artifact_id = await deliver_mod.deliver(
                text=summary,
                kind="internal",
                metadata={"action": action, "instruction": instruction, "worker": worker},
                origin_department=worker,
                config=cfg,
            )
        except Exception as exc:  # delivery audit is best-effort; the work is done
            logger.debug("org.temple: deliver failed for {}: {}", action, exc)

        return {"ok": True, "action": action, "result": result, "delivered": delivered}
    except Exception as exc:  # the whole loop must never raise
        logger.warning("org.temple: delegate_internal failed for {}: {}", action, exc)
        return {"ok": False, "action": action, "result": f"error: {exc}", "delivered": False}
