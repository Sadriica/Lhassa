"""The real department executor — a department brain that actually thinks (Fase 7).

Slice 1 shipped the two-brain core with an INJECTABLE executor and a deterministic
offline stub as the default. This module is the real executor: it runs an assigned
goal through the SAME one-turn think path the rest of Lhassa uses
(``cli/agent.run_once`` and ``voice/brain_bridge.answer``) — identity + layered
memory + the router's routine provider + ``provider.complete`` — but with the
DEPARTMENT'S identity reflected in the system prompt ("You are the <department>
department brain.").

Two design rules keep it honest:

  * **Offline-testable, $0 by default.** The live model call is gated behind
    ``LHASSA_ORG_LIVE`` (default OFF), exactly like the other live-cost paths in
    the codebase (voice, memory layer 4, the live heartbeat) which all default off
    and turn on with creds/flags. With the flag off, :func:`brain_executor` returns
    a deterministic, structured offline result and never touches a model — so the
    injectable seam stays the test/offline path and the slice-1 tests keep passing
    without a real call. A test (or production) turns the flag on, or injects/mocks
    a provider, to exercise the real think path.
  * **A department failure is data, never a crash.** ``ProviderNotConfigured`` /
    budget errors (and any unexpected error) degrade to a structured result with an
    explanatory ``summary`` and ``cost_usd=0.0``. :func:`brain_executor` NEVER
    raises — a department that can't reach a model produces a ``TASK_RESULT``
    carrying the failure, not an exception that breaks the queue.

The returned dict carries the structured result the brief asks for
(``summary`` / ``department`` / ``cost_usd`` / ``model``) plus ``ok`` (whether the
department produced a result at all — always true here, since a degraded answer is
still a result). ``ok`` keeps the result shape compatible with the slice-1 stub.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import db, watchlist
from lhassa.core.episode import Episode
from lhassa.llm import router
from lhassa.llm.gateway import BudgetExceeded
from lhassa.llm.provider import ProviderNotConfigured
from lhassa.memory import load as memory_load
from lhassa.persona import load_identity

if TYPE_CHECKING:
    from lhassa.memory.load import Context

# A department's one-turn answer is a concise result, not a long job.
_ORG_MAX_TOKENS = 512

# Truthy values that turn the live model path on (default OFF → offline + $0).
_TRUTHY = {"1", "true", "yes", "on"}


def _live_enabled(config: Config) -> bool:
    """Whether the live model path is enabled (``LHASSA_ORG_LIVE``; default OFF).

    Off by default so the department executor is offline-testable and spends no
    tokens out of the box (matching the codebase's turn-on-with-creds convention).
    """
    return (os.getenv("LHASSA_ORG_LIVE", "") or "").strip().lower() in _TRUTHY


def _system_prompt(instance_id: Optional[str], department: str, ctx: "Context") -> str:
    """Persona identity + the DEPARTMENT identity + layered memory.

    The persona block grounds the voice; the department line gives this brain its
    departmental identity ("You are the <department> department brain."); the memory
    block carries the layered context. Mirrors ``brain_bridge._system_prompt`` but
    department-flavoured.
    """
    identity = load_identity(instance_id)
    department_line = (
        f"You are the {department} department brain. You execute the typed work the "
        "general assigns to your department, under your own identity — the general "
        "coordinates, you do the thinking for your domain. Reason it through and "
        "return one concise, useful result for the goal."
    )
    parts: list[str] = [identity.persona_block, department_line]
    memory_block = ctx.to_prompt()
    if memory_block:
        parts.append(memory_block)
    return "\n\n".join(parts)


def _result(
    *, summary: str, department: str, cost_usd: float, model: str, ok: bool = True
) -> dict:
    """The structured department result (the TASK_RESULT payload's ``result``)."""
    return {
        "ok": ok,
        "summary": summary,
        "department": department,
        "cost_usd": float(cost_usd),
        "model": model,
    }


async def brain_executor(
    goal: str,
    *,
    department: str,
    instance_id: Optional[str] = None,
    config: Optional[Config] = None,
) -> dict:
    """Run ``goal`` through the department's one-turn brain think path.

    Loads identity + layered memory (semantic query = ``goal``), lets the router
    pick the routine provider+model (the opencode/local heartbeat path), thinks via
    ``provider.complete`` with the department's identity in the system prompt, logs
    an ``Episode`` (``despertar_por="org"``), and returns a structured dict.

    Offline by default: with ``LHASSA_ORG_LIVE`` unset it returns a deterministic
    offline result and makes no model call. Degrades gracefully (never raises) on a
    missing provider, a spent budget, or any unexpected error.
    """
    config = config or Config()
    instance_id = (
        instance_id or getattr(config, "instance_id", None) or Config.instance_id
    )

    # Idempotent — a department may be woken before the DB is initialised.
    await db.init_db()

    ep = Episode.begin(despertar_por="org", instance_id=instance_id)
    try:
        # Offline/default path: no model call, deterministic structured result.
        if not _live_enabled(config):
            summary = (
                f"[offline] {department} department handled (live thinking disabled "
                f"— set LHASSA_ORG_LIVE=1 to think): {goal}"
            )
            ep.finish(
                decision="org: offline (LHASSA_ORG_LIVE off)",
                accion=f"dept:{department} handled offline",
                resultado=summary,
            )
            return _result(
                summary=summary, department=department, cost_usd=0.0, model="offline"
            )

        # Live think path (mirrors cli/agent.run_once + voice/brain_bridge.answer).
        ctx = await memory_load.load(
            instance_id,
            token_budget=getattr(config, "context_token_budget", 2000),
            query=goal,
        )
        trigger = watchlist.Trigger(kind="org", action="handle", params={})
        decision = router.decide(trigger, ctx, config=config)
        provider = decision.provider
        if provider is None:
            raise ProviderNotConfigured("router resolved no provider for org task")

        system = _system_prompt(instance_id, department, ctx)
        messages = [{"role": "user", "content": goal}]
        result = await provider.complete(
            messages,
            model=decision.model,
            max_tokens=_ORG_MAX_TOKENS,
            system=system,
        )

        summary = (result.text or "").strip() or "(no answer)"
        ep.finish(
            decision=decision.reason,
            accion=f"dept:{department} think",
            resultado=summary,
            costo_usd=result.cost_usd,
        )
        return _result(
            summary=summary,
            department=department,
            cost_usd=result.cost_usd,
            model=result.model or (decision.model or ""),
        )

    except (ProviderNotConfigured, BudgetExceeded) as exc:
        # Known degradation — a department that can't reach a model still returns a
        # result (carrying the failure), never an exception.
        summary = (
            f"{department} department could not think right now "
            f"({type(exc).__name__}): {exc}"
        )
        ep.finish(
            decision="org: deferred (provider/budget)",
            accion=f"dept:{department} deferred",
            resultado=summary,
        )
        return _result(summary=summary, department=department, cost_usd=0.0, model="")
    except Exception as exc:  # never raise into the queue
        logger.error(f"brain_executor failed for dept:{department}: {exc}")
        summary = f"{department} department errored: {type(exc).__name__}: {exc}"
        ep.finish(
            decision="org: error",
            accion=f"dept:{department} error",
            resultado=summary,
        )
        return _result(summary=summary, department=department, cost_usd=0.0, model="")
    finally:
        # Always log the episode; a logging failure must not break the task.
        with contextlib.suppress(Exception):
            await ep.append()
