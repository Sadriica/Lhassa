"""
``step()`` — one turn of "hace y duerme" (§2.1).

The brain is *state + step()*. A heartbeat (systemd timer) or an API call wakes
it; ``step()`` advances it one tick and returns. It checks the watchlist (ZERO
tokens); if nothing woke it, it sleeps. Otherwise, for each trigger, it opens an
Episode (observability from the first commit), runs the fixed action when the
trigger maps to one, records the episode, and marks the originating task done.

Two paths per trigger:
  • **fixed action** — deterministic, LLM-free (§2.4): run it and record.
  • **judgment** — the trigger needs *criteria*, so the model wakes (Phase 2): the
    router picks a provider+model, the layered memory is loaded within a token
    budget, and the provider thinks directly. Guarded: if no provider is usable
    (``ProviderNotConfigured``) or the daily budget is spent (``BudgetExceeded``),
    the episode degrades to "deferred" — never a crash.

Long work is out of scope — ``step()`` never blocks on a job.
"""

from typing import Optional

from lhassa.agents import verify as verify_agent
from lhassa.config import Config
from lhassa.core import actions, db, watchlist
from lhassa.core.episode import Episode
from lhassa.llm import router
from lhassa.llm.gateway import BudgetExceeded
from lhassa.llm.provider import ModelProvider, ProviderNotConfigured
from lhassa.memory import load as memory_load

# Marker for an episode that needed judgment but could not run the model.
DEFERRED_DECISION = "deferred: needs judgment"

# Cap on the judgment answer — a wake-for-criteria is short, not a long job.
_JUDGMENT_MAX_TOKENS = 512


def _build_messages(trigger: "watchlist.Trigger", ctx: "memory_load.Context") -> list[dict]:
    """Render the trigger + loaded context into a single user message."""
    objetivo = f" sobre {trigger.objetivo!r}" if trigger.objetivo else ""
    body = (
        f"Disparador: {trigger.kind}:{trigger.action}{objetivo}.\n"
        "Decide qué hacer y responde de forma concisa."
    )
    return [{"role": "user", "content": body}]


async def _judge(
    trigger: "watchlist.Trigger",
    ep: Episode,
    *,
    instance_id: str,
    config: Config,
    provider: Optional[ModelProvider],
    completion_fn,
    force_think: bool,
    force_justdo: bool,
) -> bool:
    """Run the think-path for a non-fixed trigger. Returns True if the model ran.

    On ``ProviderNotConfigured`` / ``BudgetExceeded`` the episode is finished as
    ``DEFERRED_DECISION`` and False is returned — the caller counts it as deferred.
    """
    try:
        ctx = await memory_load.load(instance_id, token_budget=config.context_token_budget)
        decision = router.decide(
            trigger, ctx, config=config,
            force_think=force_think, force_justdo=force_justdo,
        )
        chosen = provider or decision.provider
        if chosen is None:
            raise ProviderNotConfigured("router resolved no provider")

        opts: dict = {}
        if completion_fn is not None:
            opts["completion_fn"] = completion_fn
        system = ctx.to_prompt()
        if system:
            opts["system"] = system
        messages = _build_messages(trigger, ctx)

        # An objective verify spec on the trigger turns this into a *checked*
        # judgment: the cheap model answers, the LLM-free verifier inspects it,
        # and a failed check escalates cheap→smart once (the §2.8 cascade). The
        # verifier never calls a model — it only reads the produced text.
        vspec = (getattr(trigger, "params", {}) or {}).get("verify")
        decision_note = decision.reason
        if vspec:
            def _check(completion) -> bool:
                # Pure, LLM-free gate over the answer text. Errors → not good.
                return verify_agent.verify(completion.text, spec=vspec).passed

            result = await router.cascade(
                messages,
                config=config,
                provider=chosen,
                verify=_check,
                max_tokens=_JUDGMENT_MAX_TOKENS,
                **opts,
            )
            vres = verify_agent.verify(result.text, spec=vspec)
            verdict = "passed" if vres.passed else "failed"
            decision_note = f"{decision.reason} | verificado: {verdict}"
        else:
            result = await chosen.complete(
                messages,
                model=decision.model,
                max_tokens=_JUDGMENT_MAX_TOKENS,
                **opts,
            )

        ep.finish(
            decision=decision_note,
            accion=trigger.action,
            resultado=result.text,
            costo_usd=result.cost_usd,
        )
        return True
    except (ProviderNotConfigured, BudgetExceeded) as exc:
        ep.finish(
            decision=DEFERRED_DECISION,
            accion=trigger.action,
            resultado=f"deferred ({type(exc).__name__}): {exc}",
        )
        return False


async def step(
    instance_id: Optional[str] = None,
    *,
    config: Optional[Config] = None,
    provider: Optional[ModelProvider] = None,
    completion_fn=None,
    force_think: bool = False,
    force_justdo: bool = False,
) -> dict:
    """
    Advance the brain one tick. Returns a summary dict of counts:
        {"triggers": N, "actions": A, "judged": J, "deferred": D}
    No triggers → ``{"triggers": 0}`` (the brain sleeps; no episode written).

    The judgment branch wakes the model via the router. ``provider`` and
    ``completion_fn`` are offline injection seams for tests (no network when set);
    ``force_think`` / ``force_justdo`` are the ``$think`` / ``$justdo`` overrides.

    Idempotent-ish: a fixed action marks its task ``hecha`` so the next ``step()``
    finds nothing. ``init_db()`` is called here (idempotent) so callers need not.
    """
    instance_id = instance_id or Config.instance_id
    config = config or Config()
    await db.init_db()

    triggers = await watchlist.check(instance_id)
    if not triggers:
        return {"triggers": 0}

    n_actions = 0
    n_judged = 0
    n_deferred = 0

    for trigger in triggers:
        ep = Episode.begin(
            despertar_por=f"{trigger.kind}:{trigger.action}",
            instance_id=instance_id,
        )

        if trigger.maps_to_fixed_action():
            # Fixed deterministic action — no LLM (§2.4).
            result = await actions.run(trigger.action, trigger.params, ep)
            ep.finish(
                decision=trigger.action,
                accion=trigger.action,
                resultado=result,
            )
            n_actions += 1
            ran_model = False
        else:
            # Judgment — the model wakes for criteria (§2.4). Guarded so a missing
            # provider or a spent budget degrades to a deferred episode, no crash.
            ran_model = await _judge(
                trigger, ep,
                instance_id=instance_id, config=config,
                provider=provider, completion_fn=completion_fn,
                force_think=force_think, force_justdo=force_justdo,
            )
            if ran_model:
                n_judged += 1
            else:
                n_deferred += 1

        await ep.append()

        # Mark the originating task done so the next tick doesn't re-fire it.
        if trigger.task_id is not None:
            if trigger.maps_to_fixed_action() or ran_model:
                estado = "hecha"
            else:
                estado = "diferida"
            await db.set_task_state(instance_id, trigger.task_id, estado)

    return {
        "triggers": len(triggers),
        "actions": n_actions,
        "judged": n_judged,
        "deferred": n_deferred,
    }
