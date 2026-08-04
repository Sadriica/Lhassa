# SPDX-License-Identifier: AGPL-3.0-or-later
"""
dispatch.py — close the judgment loop (Phase 2).

After the model replies to a judgment trigger, this module parses the response
for a structured JSON payload and turns it into concrete effects: facts written
to the grande and tasks minted through the existing security machinery.

Design principles:
  - ``apply()`` NEVER raises. Any parse/validation error returns a "none" summary
    and changes NOTHING — today's behavior is preserved on any failure path.
  - The judgment origin is INTERNO with contagion degradation: a minted task is
    only as trusted as the trigger that caused the judgment. MUNDO begets MUNDO.
  - Only actions in the ACTIONS registry may be minted (see SECURITY INVARIANT
    comment in the task-minting loop). This is the key termination guarantee.
  - A ``delegate`` key mints a task with accion="org_run" through the SAME
    mint_task security path as new_tasks — dispatch.py never imports org (core is
    spine, org is a shell; see tests/test_architecture.py). The org_run action
    itself (a shell-layer actionpack module) lazily imports
    org.coordinator.run_goal at the NEXT tick, offline/$0 unless LHASSA_ORG_LIVE.
  - A ``script`` key is pure sugar for ``new_tasks: [{"accion": "run_script",
    "params": {"script": ...}}]`` — it mints through the IDENTICAL mint_task
    path, so ``run_script``'s own ``requires_approval=True`` (see
    ``security.allowlist``) always parks a human approval ticket before the
    script runs; there is no bespoke execution path here.
  - ``say`` is the model's verbal answer; it goes into the dispatch summary so
    step.py can include it in the episode resultado. A notify surface arrives in
    Phase 3 as a mintable action.

Layering: spine (core/). Imports: core.db, core.actions, core.task, memory.facts,
security.trust, security.approval, security.signing, config. Must NOT import
shells (cli, api, voice, notify, org, dashboard).
"""

import json
import re
from typing import Any, Optional

from loguru import logger

from lhassa.core import actions as _actions
from lhassa.core import db as _db
from lhassa.core.task import Task
from lhassa.memory import facts as _facts
from lhassa.security import approval as _approval
from lhassa.security import allowlist as _allowlist
from lhassa.security import autonomy as _autonomy
from lhassa.security import signing as _signing
from lhassa.security.trust import Origin, TrustLevel, degrade

# Per-judgment write caps (each judgment may produce at most this many rows).
_MAX_FACTS = 5
_MAX_TASKS = 5
_MAX_DELEGATES = 3  # mirrors _MAX_TASKS's spirit — a small, bounded write cap
_MAX_SCRIPTS = 1  # a judgment reply may request at most ONE PTC script
_SCRIPT_MAX_LEN = 20_000  # characters; an oversized "script" value is ignored
_FACT_CONTENT_MAX = 500   # characters; truncate, do not reject
_TAG_MAX_LEN = 50          # characters per tag
_TAGS_MAX_COUNT = 5        # tags per fact
_GOAL_OBJETIVO_MAX = 200   # characters; the objetivo stored on a minted org_run task

# The actor id stamped on every task minted by the judgment path.
_JUDGMENT_ACTOR = "judgment"


# ── JSON extraction ──────────────────────────────────────────────────────────


def _extract_json(raw_text: str) -> Optional[dict]:
    """
    Extract the FIRST balanced JSON object from ``raw_text``.

    Handles three forms without raising:
      1. Bare JSON object.
      2. Fenced in ```json ... ``` blocks.
      3. JSON embedded in surrounding prose (the common model output form).

    Returns a ``dict`` on success, ``None`` on any parse or validation failure.
    """
    if not raw_text:
        return None

    # 1. Fenced code block — try the explicit ```json ... ``` form first.
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_text)
    if fenced:
        try:
            obj = json.loads(fenced.group(1))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    # 2. Find the first balanced { ... } anywhere in the text.
    start = raw_text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(raw_text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = raw_text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        return obj
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


# ── Tag validation ───────────────────────────────────────────────────────────


def clean_tags(raw_tags: Any) -> list[str]:
    """Validate and normalise tags: at most ``_TAGS_MAX_COUNT`` short strings.

    Public since Phase 3 — actionpack actions reuse this helper to normalise
    tags without duplicating the length/count logic.
    """
    if not isinstance(raw_tags, list):
        return []
    cleaned: list[str] = []
    for item in raw_tags:
        if not isinstance(item, str):
            continue
        tag = item.strip()[:_TAG_MAX_LEN]
        if tag:
            cleaned.append(tag)
        if len(cleaned) >= _TAGS_MAX_COUNT:
            break
    return cleaned


# Backward-compatible alias for any code that referenced the private name.
_clean_tags = clean_tags


# ── Public task-minting surface ──────────────────────────────────────────────


async def mint_task(
    instance_id: str,
    accion: str,
    objetivo: str = "",
    params: dict | None = None,
    *,
    origin: "Origin",
    due_at: Optional[str] = None,
    recurrence: Optional[str] = None,
    judgment: bool = False,
) -> "tuple[str, str]":
    """Build, sign, and persist a Task with the approval frontier applied.

    Returns ``(task_id, estado)`` on success. If ``accion`` is not in the
    ``ACTIONS`` registry AND ``judgment`` is False, returns
    ``("", "refused: unknown accion '<accion>'")``. When ``judgment=True`` any
    accion is allowed — the brain thinks about it on the next tick instead of
    running a fixed deterministic action. Token cost applies.

    ``due_at`` is an ISO 8601 UTC timestamp; the task is not returned by
    ``load_pending_tasks`` until that time arrives.
    ``recurrence`` is an interval in SECONDS (string); the brain mints the next
    occurrence after marking this task "hecha".

    Any other error returns ``("", "mint_task error: <exc>")``. Never raises.

    This is extracted from ``_apply_inner``'s task-minting block so that the
    ``add_task`` actionpack action can reuse the exact same security path
    (signing + approval frontier + DB write) without duplicating the logic.
    """
    try:
        if not accion:
            return ("", "refused: empty accion")
        if not judgment and accion not in _actions.ACTIONS:
            names = sorted(_actions.ACTIONS.keys())
            return ("", f"refused: unknown accion {accion!r}. Registered: {names}")

        # Stamp the judgment flag into params so recurring tasks can recover it.
        _params = dict(params or {})
        if judgment:
            _params["_judgment"] = True

        task = Task(
            id=_db._new_id("t"),
            instance_id=instance_id,
            accion=accion,
            objetivo=objetivo or "",
            params=_params,
            origen=origin,
            estado="pendiente",
            creado_en=_db._now(),
        )
        # Autonomy only lowers friction when its durable history is available.
        # A missing/corrupt ledger must fail safe (ask for approval), not prevent
        # the task from being minted at all.
        try:
            auto = await _autonomy.should_auto_approve(
                instance_id, accion, origin.actor_id
            )
        except Exception as exc:
            logger.warning("mint_task: autonomy lookup degraded: {}", exc)
            auto = False
        needs_human = _approval.needs_approval(task, autonomy=auto)
        if needs_human:
            task.estado = "esperando_aprobacion"
            await _approval.request_approval(
                instance_id,
                tipo="task",
                payload={
                    "accion": accion,
                    "objetivo": objetivo,
                    "params": _params,
                    "actor_id": origin.actor_id or "",
                    "origen_trust": int(origin.trust),
                    "task_id": task.id,
                    "source": "mint_task",
                },
            )

        task.firma = _signing.sign_task(task)

        kwargs = task.to_db_kwargs()
        kwargs["due_at"] = due_at
        kwargs["recurrence"] = recurrence
        await _db.add_task(**kwargs)
        return (task.id, task.estado)

    except Exception as exc:
        return ("", f"mint_task error: {exc}")


async def run_task_now(
    instance_id: str,
    task_id: str,
    accion: str,
    params: dict | None = None,
    *,
    despertar_por: str = "chat",
) -> bool:
    """Run one already-minted PENDING task immediately — the heartbeat's runner, early.

    The chat surfaces use this so a no-approval task (e.g. ``research_reply``)
    answers in seconds instead of waiting for the next beat. Semantics are
    IDENTICAL to a beat-run fixed action: the same ``actions.run`` dispatcher, a
    structured Episode, and the same ``hecha`` state transition — observability
    cannot tell an early run from a beat run. Only call it for a task minted
    ``estado == "pendiente"`` (an approval-parked task must wait for its human).
    Any failure leaves the task pending so the heartbeat retries it on schedule.
    Returns whether the run happened. Never raises.
    """
    from lhassa.core.episode import Episode

    try:
        row = await _db.get_task(instance_id, task_id)
        if row is None or row.get("estado") != "pendiente":
            return False
        task = Task.from_db_row(row)
        decision = _allowlist.allow(task)
        if decision.allowed:
            authorized, reason = await _approval.authorize_execution(task)
            if not authorized:
                decision = _allowlist.AllowDecision(
                    False, reason, decision.requires_approval
                )
        if not decision.allowed:
            logger.warning(
                "run_task_now: {!r} rejected by execution gate: {}",
                task_id, decision.reason,
            )
            ep = Episode.begin(despertar_por=despertar_por, instance_id=instance_id)
            ep.finish(
                decision="rejected by local allowlist",
                accion=task.accion,
                resultado=decision.reason,
            )
            await ep.append()
            await _db.set_task_state(instance_id, task_id, "rechazada")
            return False
        # Execute the signed persisted envelope; caller arguments cannot swap it.
        ep = Episode.begin(despertar_por=despertar_por, instance_id=instance_id)
        result = await _actions.run(task.accion, dict(task.params or {}), ep)
        ep.finish(decision=task.accion, accion=task.accion, resultado=str(result)[:500])
        await ep.append()
        await _db.set_task_state(instance_id, task_id, "hecha")
        return True
    except Exception as exc:  # leave the task for the heartbeat — never raise
        logger.warning(f"run_task_now: {accion!r} failed, left pending: {exc}")
        return False


# ── Public surface ───────────────────────────────────────────────────────────


async def apply(
    raw_text: str,
    trigger: Any,
    episode: Any,
    *,
    instance_id: str,
    config: Any,
) -> str:
    """
    Parse a judgment reply and apply its structured effects.

    Returns a short human-readable summary intended to be appended to the
    episode resultado by ``step._judge``. NEVER raises — any unhandled error
    degrades to ``"dispatch: none (error)"``.

    Accepted JSON schema (all keys optional):

        {
          "say":       "verbal answer for the episode",
          "new_facts": [{"contenido": "...", "tags": ["tag"]}],
          "new_tasks": [{"accion": "registered_action", "objetivo": "...", "params": {}}],
          "delegate":  {"goal": "instruction for the org hierarchy", "department": "..."},
          "script":    "python source for the PTC engine to run"
        }

    ``delegate`` may also be a LIST of such objects (up to ``_MAX_DELEGATES``); a
    single dict is treated as a one-item list. Each item mints a task with
    accion="org_run" (goal from "goal" or the legacy "prompt" key; "department" is
    an optional hint; "high_risk" requests the org layer's own multi-person
    approval). See the "delegate" section below.

    ``script`` is PURE SUGAR for ``new_tasks: [{"accion": "run_script", "params":
    {"script": ...}}]`` — it mints through the exact same ``mint_task`` path (no
    bespoke execution here). At most ``_MAX_SCRIPTS`` (1) is honored per reply; a
    non-string, empty, or oversized value is ignored. See the "script" section
    below.

    Security invariants enforced:
      - ``tipo`` is FORCED to ``"dato"``; a model may never write a directiva
        or a pinned fact.
      - Only actions present in ``core.actions.ACTIONS`` may be minted (see
        detailed rationale in the task loop).
      - Origin trust degrades by contagion from the triggering task.
      - Approval frontier is applied per task via ``security.approval``.
    """
    try:
        return await _apply_inner(
            raw_text, trigger, episode,
            instance_id=instance_id, config=config,
        )
    except Exception:
        # Belt-and-suspenders: apply() is called from the judgment branch of
        # step() which must degrade-never-crash. Swallow everything.
        return "dispatch: none (error)"


async def _apply_inner(
    raw_text: str,
    trigger: Any,
    episode: Any,
    *,
    instance_id: str,
    config: Any,
) -> str:
    # ── Config gate ───────────────────────────────────────────────────────────
    if not config.judgment_dispatch_enabled:
        return "dispatch: disabled"

    payload = _extract_json(raw_text)
    if payload is None:
        return "dispatch: none (unparseable)"

    # ── Establish origin trust for minted tasks ───────────────────────────────
    # Trust degrades by contagion: the minted task is only as trusted as the
    # trigger that caused the judgment. ``origen_trust`` is carried on the Trigger
    # from the DB row by watchlist._from_pending_tasks.
    trigger_trust_raw = getattr(trigger, "origen_trust", int(TrustLevel.INTERNO))
    try:
        trigger_trust = TrustLevel(int(trigger_trust_raw))
    except (ValueError, TypeError):
        trigger_trust = TrustLevel.INTERNO
    minted_trust = degrade(TrustLevel.INTERNO, trigger_trust)
    task_origin = Origin(trust=minted_trust, actor_id=_JUDGMENT_ACTOR)

    parts: list[str] = []

    # ── "say" — verbal answer, echoed into the dispatch summary ──────────────
    say = payload.get("say")
    if say and isinstance(say, str):
        snippet = say.strip()[:80].replace("\n", " ")
        ellipsis = "…" if len(say.strip()) > 80 else ""
        parts.append(f'say: "{snippet}{ellipsis}"')

    # ── new_facts ─────────────────────────────────────────────────────────────
    facts_raw = payload.get("new_facts") or []
    if not isinstance(facts_raw, list):
        facts_raw = []

    # Memory hygiene: skip an exact (casefolded) content duplicate against what
    # is already in the grande — the same dedup discipline as
    # memory.consolidate.distill_episodes. Loaded once, lazily (only when there
    # is at least one candidate fact), and updated as new facts are written so
    # two duplicates in the SAME payload don't both slip through.
    existing_lower: Optional[set] = None
    if facts_raw:
        existing_lower = {
            f.contenido.strip().casefold() for f in await _facts.load_facts(instance_id)
        }

    n_facts = 0
    n_skipped_dupe = 0
    for item in facts_raw[:_MAX_FACTS]:
        if not isinstance(item, dict):
            continue
        contenido = str(item.get("contenido") or "").strip()
        if not contenido:
            continue
        # Truncate overlong content rather than rejecting it.
        contenido = contenido[:_FACT_CONTENT_MAX]
        if existing_lower is not None and contenido.casefold() in existing_lower:
            n_skipped_dupe += 1
            continue
        tags = clean_tags(item.get("tags"))
        # SECURITY: tipo is FORCED to "dato". A model may NEVER write a
        # "directiva" or a pinned fact — those shape identity and standing
        # instructions, and their authority belongs to a human author only.
        await _facts.add_fact(
            instance_id,
            "dato",
            contenido,
            pinned=False,
            tags=tags,
            procedencia=f"judgment:{episode.id}",
            confianza=0.7,
        )
        if existing_lower is not None:
            existing_lower.add(contenido.casefold())
        n_facts += 1

    if n_facts:
        parts.append(f"{n_facts} fact{'s' if n_facts != 1 else ''}")
    if n_skipped_dupe:
        parts.append(f"{n_skipped_dupe} dupe fact{'s' if n_skipped_dupe != 1 else ''} skipped")

    # ── new_tasks ─────────────────────────────────────────────────────────────
    tasks_raw = payload.get("new_tasks") or []
    if not isinstance(tasks_raw, list):
        tasks_raw = []

    n_tasks = 0
    n_awaiting = 0
    skipped_unknown: list[str] = []

    for item in tasks_raw[:_MAX_TASKS]:
        if not isinstance(item, dict):
            continue
        accion = str(item.get("accion") or "").strip()
        objetivo = str(item.get("objetivo") or "").strip()
        params = item.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        # SECURITY INVARIANT: Only accions present in the ACTIONS registry
        # may be minted. An accion not in the registry is SKIPPED.
        #
        # Rationale: a non-fixed accion would re-enter the judgment branch on
        # the very next tick (because ``Trigger.maps_to_fixed_action()`` returns
        # False for unknown names), causing the brain to think → dispatch →
        # think → … in an infinite loop that costs tokens and never terminates.
        # Only registered, deterministic fixed actions are safe to mint here:
        # they run in code with ZERO tokens, mark themselves "hecha", and never
        # re-trigger the model. This invariant makes all judgment-dispatched
        # work terminate deterministically.
        if not accion or accion not in _actions.ACTIONS:
            if accion:
                skipped_unknown.append(accion)
            continue

        # Delegate signing + approval + DB write to mint_task (extracted so that
        # the add_task actionpack action reuses the same security path).
        task_id, estado = await mint_task(
            instance_id,
            accion,
            objetivo=objetivo,
            params=params,
            origin=task_origin,
        )
        if not task_id:
            # mint_task already refused for a reason other than unknown accion
            # (shouldn't happen after the registry check above, but belt-and-suspenders)
            continue

        if estado == "esperando_aprobacion":
            n_awaiting += 1
        n_tasks += 1

    if n_tasks:
        task_str = f"{n_tasks} task{'s' if n_tasks != 1 else ''}"
        if n_awaiting:
            task_str += f" ({n_awaiting} awaiting approval)"
        parts.append(task_str)
    for accion in skipped_unknown:
        parts.append(f"skipped: unknown accion {accion!r}")

    # ── script ────────────────────────────────────────────────────────────────
    # PURE SUGAR for new_tasks: [{"accion": "run_script", "params": {"script": ...}}].
    # Routed through the IDENTICAL mint_task path used by new_tasks/delegate above
    # — no bespoke execution here. run_script's own requires_approval=True (see
    # security.allowlist) means a human always approves the ticket before the
    # script actually runs. At most _MAX_SCRIPTS (1) per reply; a list, a
    # non-string, an empty, or an oversized value is ignored defensively.
    script_raw = payload.get("script")
    if isinstance(script_raw, str):
        script = script_raw.strip()
        if not script:
            logger.debug("dispatch: 'script' key present but empty, ignored")
        elif len(script) > _SCRIPT_MAX_LEN:
            logger.debug(
                f"dispatch: 'script' key exceeds {_SCRIPT_MAX_LEN} chars, ignored"
            )
        else:
            task_id, estado = await mint_task(
                instance_id,
                "run_script",
                objetivo="judgment-authored script",
                params={"script": script},
                origin=task_origin,
            )
            if task_id:
                if estado == "esperando_aprobacion":
                    parts.append("1 script minted (awaiting approval)")
                else:
                    parts.append("1 script minted")
    elif script_raw is not None:
        logger.debug(
            f"dispatch: 'script' key is not a string ({type(script_raw).__name__}), ignored"
        )

    # ── delegate ──────────────────────────────────────────────────────────────
    # A well-formed delegate request mints a TASK (accion="org_run"), NOT an
    # inline execution — dispatch.py stays spine-only (no org import). The task
    # runs through the SAME mint_task security path as new_tasks (signing +
    # approval frontier + DB write); the org_run action (a shell) resolves it at
    # the next tick. Accepts a single dict or a list of dicts (bounded).
    delegate_raw = payload.get("delegate")
    if isinstance(delegate_raw, dict):
        delegate_items: list = [delegate_raw]
    elif isinstance(delegate_raw, list):
        delegate_items = delegate_raw
    else:
        delegate_items = []

    n_delegates = 0
    for item in delegate_items[:_MAX_DELEGATES]:
        if not isinstance(item, dict):
            continue
        goal = str(item.get("goal") or item.get("prompt") or "").strip()
        if not goal:
            continue

        delegate_params: dict = {"goal": goal}
        department = item.get("department")
        if isinstance(department, str) and department.strip():
            delegate_params["department"] = department.strip()
        if item.get("high_risk"):
            delegate_params["high_risk"] = True

        # "org_run" is registered by actionpack at process start (same invariant
        # as every other action new_tasks mints) — see actionpack/_org.py.
        task_id, estado = await mint_task(
            instance_id,
            "org_run",
            objetivo=goal[:_GOAL_OBJETIVO_MAX],
            params=delegate_params,
            origin=task_origin,
        )
        if task_id:
            n_delegates += 1

    if n_delegates:
        parts.append(
            f"{n_delegates} delegate{'s' if n_delegates != 1 else ''} minted"
        )

    if not parts:
        return "dispatch: none"

    return "dispatched: " + ", ".join(parts)
