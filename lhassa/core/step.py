"""
``step()`` — one turn of "hace y duerme" (§2.1).

The brain is *state + step()*. A heartbeat (systemd timer) or an API call wakes
it; ``step()`` advances it one tick and returns. It checks the watchlist (ZERO
tokens); if nothing woke it, it sleeps. Otherwise, for each trigger, it opens an
Episode (observability from the first commit), runs the fixed action when the
trigger maps to one, records the episode, and marks the originating task done.

This Phase 0 slice is deterministic and LLM-free: every trigger either maps to a
fixed action (run it) or is **deferred** to the Phase 2 router (recorded as
"needs judgment", no model called). Long work is out of scope — ``step()`` never
blocks on a job.
"""

from typing import Optional

from lhassa.config import Config
from lhassa.core import actions, db, watchlist
from lhassa.core.episode import Episode

# Marker the Phase 2 router branch will key off of in the episode log.
DEFERRED_DECISION = "deferred: needs judgment"


async def step(instance_id: Optional[str] = None) -> dict:
    """
    Advance the brain one tick. Returns a summary dict of counts:
        {"triggers": N, "actions": A, "deferred": D}
    No triggers → ``{"triggers": 0}`` (the brain sleeps; no episode written).

    Idempotent-ish: a fixed action marks its task ``hecha`` so the next ``step()``
    finds nothing. ``init_db()`` is called here (idempotent) so callers need not.
    """
    instance_id = instance_id or Config.instance_id
    await db.init_db()

    triggers = await watchlist.check(instance_id)
    if not triggers:
        return {"triggers": 0}

    n_actions = 0
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
        else:
            # TODO: Phase 2 router — load layered memory within a token budget,
            # let router.decide(trigger, ctx) pick provider-or-harness, run via
            # the executor, and verify the result. For now we record the trigger
            # as needing judgment and call NO model.
            ep.finish(
                decision=DEFERRED_DECISION,
                accion=trigger.action,
                resultado=f"deferred to router: {trigger.action!r} on {trigger.objetivo!r}",
            )
            n_deferred += 1

        await ep.append()

        # Mark the originating task done so the next tick doesn't re-fire it.
        if trigger.task_id is not None:
            estado = "hecha" if trigger.maps_to_fixed_action() else "diferida"
            await db.set_task_state(instance_id, trigger.task_id, estado)

    return {
        "triggers": len(triggers),
        "actions": n_actions,
        "deferred": n_deferred,
    }
