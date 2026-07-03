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
  - A ``delegate`` key is parsed and recorded but NOT yet wired to an executor;
    Phase 6 adds the handler. The parsed representation is complete now so
    Phase 6 only needs to add a handler, not change the parsing.
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

from lhassa.core import actions as _actions
from lhassa.core import db as _db
from lhassa.core.task import Task
from lhassa.memory import facts as _facts
from lhassa.security import approval as _approval
from lhassa.security import signing as _signing
from lhassa.security.trust import Origin, TrustLevel, degrade

# Per-judgment write caps (each judgment may produce at most this many rows).
_MAX_FACTS = 5
_MAX_TASKS = 5
_FACT_CONTENT_MAX = 500   # characters; truncate, do not reject
_TAG_MAX_LEN = 50          # characters per tag
_TAGS_MAX_COUNT = 5        # tags per fact

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


def _clean_tags(raw_tags: Any) -> list[str]:
    """Validate and normalise tags: at most ``_TAGS_MAX_COUNT`` short strings."""
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
          "delegate":  {"prompt": "instruction for a coding harness"}
        }

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

    n_facts = 0
    for item in facts_raw[:_MAX_FACTS]:
        if not isinstance(item, dict):
            continue
        contenido = str(item.get("contenido") or "").strip()
        if not contenido:
            continue
        # Truncate overlong content rather than rejecting it.
        contenido = contenido[:_FACT_CONTENT_MAX]
        tags = _clean_tags(item.get("tags"))
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
        n_facts += 1

    if n_facts:
        parts.append(f"{n_facts} fact{'s' if n_facts != 1 else ''}")

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

        # Build and sign the Task — mirroring the checkin_routes pattern exactly.
        task = Task(
            id=_db._new_id("t"),
            instance_id=instance_id,
            accion=accion,
            objetivo=objetivo,
            params=params,
            origen=task_origin,
            estado="pendiente",
            creado_en=_db._now(),
        )
        task.firma = _signing.sign_task(task)

        # Apply the approval frontier — HITL per flow, not global.
        needs_human = _approval.needs_approval(task)
        if needs_human:
            task.estado = "esperando_aprobacion"
            await _approval.request_approval(
                instance_id,
                tipo="task",
                payload={
                    "accion": accion,
                    "objetivo": objetivo,
                    "params": params,
                    "actor_id": _JUDGMENT_ACTOR,
                    "origen_trust": int(minted_trust),
                    "task_id": task.id,
                    "source": f"judgment:{episode.id}",
                },
            )
            n_awaiting += 1

        await _db.add_task(**task.to_db_kwargs())
        n_tasks += 1

    if n_tasks:
        task_str = f"{n_tasks} task{'s' if n_tasks != 1 else ''}"
        if n_awaiting:
            task_str += f" ({n_awaiting} awaiting approval)"
        parts.append(task_str)
    for accion in skipped_unknown:
        parts.append(f"skipped: unknown accion {accion!r}")

    # ── delegate ──────────────────────────────────────────────────────────────
    # Parsed and recorded now; the actual executor is wired in Phase 6.
    # The parsed representation (a dict with at minimum "prompt") is complete
    # here so Phase 6 only needs to add a handler, not change this parsing.
    delegate = payload.get("delegate")
    if delegate and isinstance(delegate, dict):
        parts.append("delegate requested (not yet wired)")

    if not parts:
        return "dispatch: none"

    return "dispatched: " + ", ".join(parts)
