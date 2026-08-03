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

Long work is NOT done inline — when the router decides to **delegate** code work
to a harness, ``step()`` LAUNCHES it as a separate process (recording "job PID X")
and returns immediately. A later tick's ``runner.supervise()`` reaps it via disk;
``step()`` never blocks on a job ("hace y duerme", §2.1).
"""

from typing import Optional

from loguru import logger

from lhassa.agents import verify as verify_agent
from lhassa.config import Config
from lhassa.core import actions, db, dispatch, policy, watchlist
from lhassa.core.episode import Episode
from lhassa.harness import runner
from lhassa.harness.adapter import get_adapter
from lhassa.llm import router
from lhassa.llm.gateway import BudgetExceeded
from lhassa.llm.provider import ModelProvider, ProviderNotConfigured
from lhassa.memory import load as memory_load

# Marker for an episode that needed judgment but could not run the model.
DEFERRED_DECISION = "deferred: needs judgment"

# Cap on the judgment answer — raised from 512 to 768 to accommodate JSON dispatch
# payloads (new_facts / new_tasks objects add meaningful token overhead).
_JUDGMENT_MAX_TOKENS = 768


def _build_messages(trigger: "watchlist.Trigger", ctx: "memory_load.Context") -> list[dict]:
    """Render the trigger + loaded context into a single user message.

    The instruction asks the model to optionally reply with a JSON dispatch
    payload alongside (or instead of) prose. Plain text is always acceptable.

    Feedback-threading seam (Wave A, TASK A2): when this turn belongs to a
    verify-terminated loop (``trigger.params["_loop_id"]``) and the PREVIOUS
    turn's verifier failed, ``_judge`` stamps that verdict's ``detail`` onto
    ``trigger.params["_loop_feedback"]`` before minting the next recurrence — the
    SAME trigger.params dict the recurrence-mint block in ``step()`` copies
    forward. This is the ONE place that text is read back into the prompt.
    """
    objetivo = f" sobre {trigger.objetivo!r}" if trigger.objetivo else ""
    body = (
        f"Disparador: {trigger.kind}:{trigger.action}{objetivo}.\n"
        "Decide qué hacer y responde de forma concisa.\n\n"
        "Para despachar efectos, incluye un objeto JSON en tu respuesta "
        "(texto antes/después es válido). Esquema, todas las claves opcionales:\n"
        '{"say":"respuesta verbal",'
        '"new_facts":[{"contenido":"...","tags":["etiqueta"]}],'
        '"new_tasks":[{"accion":"nombre_accion_fija","objetivo":"...","params":{}}],'
        '"delegate":{"goal":"instruccion para la jerarquia org"},'
        '"script":"codigo python que llama lhassa_actions"}\n'
        "Restricciones: accion debe estar en el registro fijo de acciones; "
        "new_facts son solo tipo dato, nunca directiva ni pinned; máx 5 facts, 5 tasks. "
        "script: SOLO para cadenas de ≥3 acciones deterministas del registro — se "
        "convierte en una tarea run_script que un humano aprueba antes de correr; "
        "máx 1 por respuesta. Sin efectos: texto plano es válido."
    )
    loop_feedback = (getattr(trigger, "params", None) or {}).get("_loop_feedback")
    if loop_feedback:
        body += (
            "\n\nEste objetivo tiene un verificador objetivo. El intento anterior "
            f"NO pasó: {loop_feedback}\nAjusta tu respuesta para intentar cumplirlo."
        )
    return [{"role": "user", "content": body}]


# Fewer recent episodes for a "minimal" context load (a routine/default judgment
# with no reflex flagging it hard) than the full-tier default (memory_load.load's
# own default of 20).
_MINIMAL_RECENT_N = 5
_FULL_RECENT_N = 20


def _judgment_query(trigger: "watchlist.Trigger") -> str:
    """The semantic-layer query text for a trigger: its objetivo, else kind:action."""
    return trigger.objetivo or f"{trigger.kind}:{trigger.action}"


def next_recurrence_due(old_due, now, interval_seconds: int):
    """The next occurrence of a recurring task — ADVANCED IN WHOLE INTERVALS.

    A bare ``max(now, old_due + interval)`` looks equivalent and is not: on a
    machine that suspends (a laptop), any downtime longer than one interval
    makes every overdue recurrence re-anchor to the WAKE INSTANT instead of its
    wall-clock slot. An 08:00 daily digest then walks to whenever the lid was
    opened — and because every overdue task re-anchors to the SAME instant,
    schedules that were deliberately apart (08:00 + 21:00) collapse onto each
    other and fire as duplicates from then on. That is not hypothetical: it is
    how the owner's twice-daily digest ended up arriving twice, seconds apart.

    Stepping by whole intervals keeps 08:00 at 08:00 across any downtime.
    Occurrences missed while suspended stay MISSED — they are deliberately not
    replayed in a catch-up burst, which would be a notification storm on wake.

    A non-positive ``interval_seconds`` degrades to ``now`` rather than looping.
    """
    from datetime import timedelta as _td

    if interval_seconds <= 0:
        return now
    next_due = old_due + _td(seconds=interval_seconds)
    if next_due <= now:
        missed = (now - next_due).total_seconds() // interval_seconds + 1
        next_due += _td(seconds=interval_seconds * missed)
    return next_due


async def _notify_loop_capped(instance_id: str, loop_id: str, goal: str) -> None:
    """Notify the owner once via the existing notify path — the registered
    ``notify`` fixed action (``actionpack/_brain.py:notify``), so ``core/``
    (spine) never imports ``notify/`` (shell) directly; the registry indirection
    is the seam. Best-effort: a delivery failure must never break the tick.
    """
    try:
        await actions.run(
            "notify",
            {
                "text": (
                    f"Goal loop {loop_id} capped: {goal!r} did not verify-pass "
                    "within its turn budget. Recurrence stopped."
                ),
                "level": "warning",
            },
            None,
        )
    except Exception as exc:
        logger.debug(f"step: could not notify owner of capped loop {loop_id}: {exc}")


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
    context_tier: str = "full",
) -> bool:
    """Run the think-path for a non-fixed trigger. Returns True if the model ran.

    ``context_tier`` ("minimal" or "full", from the deterministic policy layer —
    ``core/policy.py``) sizes the memory load: "minimal" uses
    ``config.context_token_budget_minimal`` and fewer recent episodes; "full" uses
    the standard ``config.context_token_budget``. The load always passes a
    ``query`` (the trigger's objetivo, so the semantic/tag layers can actually
    fire) and ``active_tags=[trigger.kind]``.

    On ``ProviderNotConfigured`` / ``BudgetExceeded`` the router's fallback CHAIN
    (``lhassa.llm.router.fallback_chain`` — the owner's CLI subscriptions before
    cloud) is walked before giving up; only if every entry in the chain also
    fails does the episode finish as ``DEFERRED_DECISION`` and False is returned
    (the caller counts it as deferred).

    **Loop runtime (Wave A, TASK A2)** — when ``trigger.params["_loop_id"]`` is
    set (stamped by ``/goal --verify ...``, see ``notify/commands.py:_cmd_goal``),
    this turn is one iteration of a bounded, verifier-terminated loop
    (``core/db.py``'s ``loops`` table):
      - The turn cap is enforced BEFORE any model call: a loop already at
        ``max_turns`` is marked ``status="capped"``, the owner is notified once,
        recurrence is stopped (``trigger.recurrence`` cleared so ``step()``'s
        recurrence-mint block below is a no-op), and the model never runs.
      - Otherwise the turn proceeds as normal, and AFTER the model answers, the
        loop's ``verifier_spec`` (``agents/verify.py`` — deterministic, $0-first,
        never an LLM) checks the reply text: a PASS marks the loop ``"done"`` and
        stops recurrence (goal reached); a FAIL marks it ``"iterate"`` (turn
        bumped) and threads the verifier's ``detail`` into
        ``trigger.params["_loop_feedback"]`` — the SAME trigger object the
        recurrence-mint block below copies into the next occurrence's params, so
        the next turn's prompt (see ``_build_messages``) reads the previous
        verdict. A missing/broken loop row degrades to a normal, unbounded
        judgment turn — this feature must never break a plain ``/goal``.
    """
    loop_id = (getattr(trigger, "params", None) or {}).get("_loop_id")
    loop_row: Optional[dict] = None
    if loop_id:
        try:
            loop_row = await db.get_loop(instance_id, loop_id)
        except Exception as exc:
            logger.debug(f"step: could not load loop {loop_id!r}: {exc}")
            loop_row = None

        if loop_row is not None and loop_row.get("status") in ("done", "capped", "failed"):
            # Terminal already (e.g. a stray extra recurrence) — stop it here too.
            trigger.recurrence = None
            ep.finish(
                decision=f"loop {loop_id} already {loop_row.get('status')}",
                accion=trigger.action,
                resultado="loop already terminal; recurrence stopped",
            )
            return True

        if loop_row is not None:
            max_turns = int(loop_row.get("max_turns") or 10)
            turn = int(loop_row.get("turn") or 0)
            if turn >= max_turns:
                # Cap enforced BEFORE any work — the model never runs this turn.
                try:
                    await db.advance_loop(instance_id, loop_id, status="capped")
                except Exception as exc:
                    logger.debug(f"step: could not cap loop {loop_id!r}: {exc}")
                trigger.recurrence = None
                await _notify_loop_capped(instance_id, loop_id, trigger.objetivo or "")
                ep.finish(
                    decision=f"loop {loop_id} capped at {max_turns} turns",
                    accion=trigger.action,
                    resultado="turn cap reached before work; recurrence stopped",
                )
                return True

    try:
        minimal = context_tier == "minimal"
        token_budget = (
            config.context_token_budget_minimal if minimal else config.context_token_budget
        )
        recent_n = _MINIMAL_RECENT_N if minimal else _FULL_RECENT_N
        ctx = await memory_load.load(
            instance_id,
            token_budget=token_budget,
            recent_n=recent_n,
            active_tags=[trigger.kind],
            query=_judgment_query(trigger),
            config=config,
        )
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

        last_exc: Optional[Exception] = None
        result = None

        def _run_one(attempt_provider, attempt_model, opts_local):
            if vspec:
                def _check(completion) -> bool:
                    # Pure, LLM-free gate over the answer text. Errors → not good.
                    return verify_agent.verify(completion.text, spec=vspec).passed

                return router.cascade(
                    messages,
                    config=config,
                    provider=attempt_provider,
                    verify=_check,
                    max_tokens=_JUDGMENT_MAX_TOKENS,
                    **opts_local,
                )
            return attempt_provider.complete(
                messages,
                model=attempt_model,
                max_tokens=_JUDGMENT_MAX_TOKENS,
                **opts_local,
            )

        # 1) Try the router's own choice first.
        try:
            result = await _run_one(chosen, decision.model, opts)
        except (ProviderNotConfigured, BudgetExceeded) as exc:
            last_exc = exc
        except Exception as exc:  # a live provider can fail in untyped ways too
            logger.warning(f"step: primary provider {chosen.name!r} failed: {exc}")
            last_exc = exc

        # 2) Resilience fallback CHAIN — only when the caller did NOT pin an
        # explicit provider (``provider`` is the offline injection seam tests/CLI
        # overrides use to force one exact provider deterministically; honoring
        # that pin keeps the seam meaningful and never silently reaches into the
        # real config-driven chain). On BudgetExceeded only the $0 CLI providers
        # are tried (paying cloud again would just hit the same exhausted
        # budget); on ProviderNotConfigured (or any other failure) the full
        # configured chain is walked, including the cloud gateway.
        attempts: list[tuple] = []
        if result is None and provider is None:
            chain = router.fallback_chain(config, chosen)
            if isinstance(last_exc, BudgetExceeded):
                chain = [
                    (p, m) for p, m in chain if getattr(p, "name", "") in router.CLI_PROVIDERS
                ]
            attempts = chain

        for attempt_provider, attempt_model in attempts:
            try:
                result = await _run_one(attempt_provider, attempt_model, opts)
                decision_note = (
                    f"{decision.reason} | fallback: {getattr(attempt_provider, 'name', '?')}"
                )
                break
            except (ProviderNotConfigured, BudgetExceeded) as exc:
                last_exc = exc
                result = None
                continue
            except Exception as exc:
                # A live provider/CLI can fail in ways neither of the two typed
                # exceptions cover (a real network error, an auth failure, a CLI
                # crash) — this must degrade to the next attempt (or defer), the
                # same as ``notify/telegram_inbound._brain_reply``'s chain walk,
                # never propagate and crash the tick.
                logger.warning(
                    f"step: provider {getattr(attempt_provider, 'name', '?')!r} "
                    f"failed in the judgment fallback chain: {exc}"
                )
                last_exc = exc
                result = None
                continue

        # A checked (verify-spec) judgment folds the pass/fail verdict into the
        # decision note, whichever attempt (primary or fallback) produced ``result``.
        if result is not None and vspec:
            vres = verify_agent.verify(result.text, spec=vspec)
            verdict = "passed" if vres.passed else "failed"
            decision_note = f"{decision_note} | verificado: {verdict}"

        if result is None:
            # Always surface as one of the two typed, degrade-safe exceptions the
            # outer except below (and the caller) already handle — an arbitrary
            # exception type from a live provider/CLI must never escape and crash
            # the tick.
            if isinstance(last_exc, (ProviderNotConfigured, BudgetExceeded)):
                raise last_exc
            raise ProviderNotConfigured(
                f"every provider in the judgment fallback chain failed: {last_exc}"
            )

        # Close the loop: parse the model's reply for structured effects (facts +
        # tasks) and apply them through the security machinery. Verifiers already
        # inspected result.text above (raw, unmodified). dispatch.apply() NEVER
        # raises; a meaningful summary is appended to the episode resultado only
        # when something was actually dispatched (avoids resultado clutter).
        dispatch_summary = await dispatch.apply(
            result.text, trigger, ep,
            instance_id=instance_id, config=config,
        )
        dispatch_note = (
            f" | {dispatch_summary}"
            if dispatch_summary.startswith("dispatched:")
            else ""
        )

        # Loop verify-and-terminate (Wave A, TASK A2): a PASS ends the loop
        # ("done") and stops recurrence; a FAIL bumps the turn ("iterate") and
        # threads the verifier's detail into trigger.params["_loop_feedback"] for
        # the recurrence-mint block below to carry into the next occurrence. A
        # loop row with no verifier_spec (shouldn't happen — /goal always sets
        # one when it creates a loop) is left alone rather than guessed at.
        loop_note = ""
        loop_vspec = loop_row.get("verifier_spec") if loop_row is not None else None
        if loop_id and loop_vspec:
            lvres = verify_agent.verify(result.text, spec=loop_vspec)
            try:
                if lvres.passed:
                    await db.advance_loop(
                        instance_id, loop_id,
                        status="done", verdict={"passed": True, "detail": lvres.detail},
                    )
                    trigger.recurrence = None
                    loop_note = f" | loop {loop_id}: done (verified)"
                else:
                    await db.advance_loop(
                        instance_id, loop_id,
                        status="iterate",
                        verdict={"passed": False, "detail": lvres.detail},
                        bump_turn=True,
                    )
                    trigger.params = dict(trigger.params or {})
                    trigger.params["_loop_feedback"] = lvres.detail
                    loop_note = f" | loop {loop_id}: iterate (verify failed)"
            except Exception as exc:
                # Loop bookkeeping must never break a normal judgment turn.
                logger.debug(f"step: loop {loop_id!r} advance failed: {exc}")

        ep.finish(
            decision=decision_note,
            accion=trigger.action,
            resultado=result.text + dispatch_note + loop_note,
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


def _delegation_prompt(trigger: "watchlist.Trigger") -> str:
    """Render the trigger into a single instruction for the coding muscle."""
    objetivo = f" sobre {trigger.objetivo!r}" if trigger.objetivo else ""
    extra = (trigger.params or {}).get("prompt") or (trigger.params or {}).get("instruccion")
    if extra:
        return str(extra)
    return f"Tarea de código: {trigger.action}{objetivo}."


async def _delegate(
    trigger: "watchlist.Trigger",
    ep: Episode,
    decision: "router.Decision",
    *,
    instance_id: str,
) -> str:
    """Launch the delegated code work on a harness and record the episode.

    Fire-and-forget: ``runner.launch`` starts the harness as a *separate* process
    and returns its job_id without blocking; the episode notes the delegation, the
    job_id and the PID. ``runner.supervise`` (called each tick) reaps it later.
    Returns the job_id.
    """
    adapter = get_adapter(decision.harness)
    prompt = _delegation_prompt(trigger)
    job_id = await runner.launch(adapter, prompt, instance_id=instance_id)
    pid = runner.running_pid(job_id)
    ep.finish(
        decision=decision.reason,
        accion=trigger.action,
        resultado=f"delegado a {decision.harness}: job {job_id} (PID {pid})",
        costo_usd=0.0,
    )
    return job_id


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

    # Reap any delegated jobs that finished since the last tick (cheap; disk-only
    # supervision — never blocks). This is how long work is followed across ticks.
    reaped = await runner.supervise(instance_id)

    # Deferred-task retry sweep: a task the judgment path could not run (no
    # provider / budget spent) sits "diferida" — re-queue it once the backoff
    # window has passed, or reject it (terminal) once it has exhausted its
    # retries. Wrapped defensively: a sweep failure must never crash a tick.
    n_rejected = 0
    try:
        sweep = await db.sweep_deferred_tasks(
            instance_id,
            backoff_seconds=config.deferred_retry_backoff_seconds,
            max_retries=config.deferred_max_retries,
        )
        for rejected_id in sweep.get("rejected_ids", []):
            try:
                rej_ep = Episode.begin(
                    despertar_por="task-retry-exhausted", instance_id=instance_id
                )
                rej_ep.finish(
                    decision="deferred retry exhausted",
                    accion="reject_task",
                    resultado=(
                        f"task {rejected_id} exceeded "
                        f"{config.deferred_max_retries} retries; marked rechazada"
                    ),
                )
                await rej_ep.append()
                n_rejected += 1
            except Exception as exc:
                logger.warning(
                    "step: could not log rejection episode for task {}: {}",
                    rejected_id, exc,
                )
    except Exception as exc:
        logger.warning("step: deferred retry sweep failed: {}", exc)

    triggers = await watchlist.check(instance_id)
    if not triggers:
        summary = {"triggers": 0}
        if reaped:
            summary["reaped"] = len(reaped)
        if n_rejected:
            summary["rejected"] = n_rejected
        return summary

    n_actions = 0
    n_judged = 0
    n_deferred = 0
    n_delegated = 0
    n_skipped = 0

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
            # Deterministic policy layer FIRST (§ core/policy.py "reflexes"):
            # zero-token triage before the router/judgment path even loads memory.
            # NEVER raises — degrades to think/minimal on any error.
            pdecision = policy.evaluate(trigger)

            if pdecision.kind == "action":
                result = await actions.run(
                    pdecision.action, pdecision.params, ep
                )
                ep.finish(
                    decision=pdecision.reason,
                    accion=pdecision.action or trigger.action,
                    resultado=result,
                )
                n_actions += 1
                ran_model = False
            elif pdecision.kind == "skip":
                ep.finish(
                    decision=pdecision.reason,
                    accion=trigger.action,
                    resultado=pdecision.reason,
                )
                n_skipped += 1
                ran_model = True  # handled — the task is done, not deferred
            else:
                # "think" — thread the policy's hardness hint into the trigger so
                # the router's existing trigger.params["hard"] seam picks it up
                # (least-code path; force_think/force_justdo still win if set).
                if pdecision.hard:
                    trigger.params = {**(trigger.params or {}), "hard": True}

                # First, a free rules-based decide: is this delegated CODE work
                # (Axis 2) or direct thinking (Axis 1)?
                decision = router.decide(
                    trigger, None, config=config,
                    force_think=force_think, force_justdo=force_justdo,
                )
                if decision.kind == "delegate":
                    # Long work — LAUNCH a harness process and return; do NOT block.
                    await _delegate(trigger, ep, decision, instance_id=instance_id)
                    n_delegated += 1
                    ran_model = True
                else:
                    # Judgment — the model wakes for criteria. Guarded so a missing
                    # provider or a spent budget degrades to a deferred episode.
                    ran_model = await _judge(
                        trigger, ep,
                        instance_id=instance_id, config=config,
                        provider=provider, completion_fn=completion_fn,
                        force_think=force_think, force_justdo=force_justdo,
                        context_tier=pdecision.context_tier,
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

            # Recurrence: when a recurring task completes, mint the next occurrence
            # immediately. The next due_at = max(now, old_due + interval) so a
            # long downtime never queues a backlog of overdue beats.
            if estado == "hecha" and trigger.recurrence:
                try:
                    _interval = float(trigger.recurrence)
                    from datetime import datetime, timezone
                    now_dt = datetime.now(timezone.utc)
                    if trigger.due_at:
                        try:
                            old_due = datetime.fromisoformat(
                                trigger.due_at.replace("Z", "+00:00")
                            )
                        except (ValueError, AttributeError):
                            old_due = now_dt
                    else:
                        old_due = now_dt
                    next_due = next_recurrence_due(old_due, now_dt, _interval)
                    next_due_iso = next_due.isoformat()
                    from lhassa.security.trust import Origin, TrustLevel
                    origin = Origin(
                        trust=TrustLevel(trigger.origen_trust),
                        actor_id=trigger.origen_actor,
                    )
                    _params = dict(trigger.params or {})
                    # Preserve the judgment flag for recurring judgment tasks.
                    if trigger.judgment:
                        _params["_judgment"] = True
                    from lhassa.core import dispatch
                    await dispatch.mint_task(
                        instance_id,
                        trigger.action,
                        objetivo=trigger.objetivo or "",
                        params=_params,
                        origin=origin,
                        due_at=next_due_iso,
                        recurrence=trigger.recurrence,
                        judgment=trigger.judgment,
                    )
                except Exception as exc:
                    # Recurrence minting must never crash step().
                    logger.warning(
                        "step: recurrence mint failed for task {}: {}",
                        trigger.task_id,
                        exc,
                    )

    summary = {
        "triggers": len(triggers),
        "actions": n_actions,
        "judged": n_judged,
        "deferred": n_deferred,
        "delegated": n_delegated,
        "skipped": n_skipped,
    }
    if reaped:
        summary["reaped"] = len(reaped)
    if n_rejected:
        summary["rejected"] = n_rejected
    return summary
