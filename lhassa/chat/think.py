# SPDX-License-Identifier: AGPL-3.0-or-later
"""The chat door's tool-less brain think.

Answers one owner message: identity + layered memory (the message as the
semantic query) + the self-card, the router's routine provider (or the
``telegram_provider`` pin), ``provider.complete`` with NO tools, then the
fallback CHAIN (the owner's CLI subscriptions before the cloud gateway). The
same shape as :func:`lhassa.voice.brain_bridge.answer`, logging a
``despertar_por="telegram"`` episode.

A tool-less think is deliberate: routing chat through the full 26-tool agent on
the cheap heartbeat backend produced malformed tool calls and failed replies,
whereas a plain completion on the routine provider answers reliably.
"""

from __future__ import annotations

import contextlib
import shutil  # noqa: F401 - re-exported so callers/tests can monkeypatch
from typing import Optional

from loguru import logger

from lhassa import persona as _persona
from lhassa.chat import markers, state
from lhassa.chat.types import CHAT_SOURCE, Sender

# The fallback CHAIN builder lives in ``lhassa.llm.router`` (the interface
# layer) so both this shell and the spine (``core/step.py``'s judgment path)
# walk the SAME "owner's CLI subscriptions before cloud" chain. Imported under
# the name tests monkeypatch.
from lhassa.llm.router import fallback_chain as _fallback_chain

# A chat turn is a short answer, not a long job. The actual per-turn cap now
# comes from the resolved effort profile (Plan 4, Stream 1; see
# ``lhassa.notify.task_router``); this stays only as the retry floor below.
# A reasoning model (deepseek V4 Flash) spends its budget THINKING before it
# writes; when the cap is hit mid-thought the content comes back EMPTY with real
# tokens billed. One bounded retry with a bigger cap almost always recovers it.
_CHAT_MAX_TOKENS_RETRY = 3072
_EMPTY_REPLY = (
    "My reply came out empty, I overthought it. Ask me again (shorter works "
    "best) and I'll answer directly."
)

# Short fallback when the brain can't reach a model: sent verbatim to the chat.
_FALLBACK_REPLY = "I can't reach my model right now, give it a moment and ask me again."

# Marker for an episode whose model could not run (mirrors core/step + voice).
_DEFERRED_DECISION = "deferred: telegram needs judgment"

# Chat-output instruction layered on the persona (plain text for a chat bubble).
# The honesty block exists because this turn is TOOL-LESS: without it the model
# roleplays capabilities it lacks ("I'm searching…", "I'll update you"), promises
# nothing will ever keep. The [[research: …]] marker is the ONE real escape hatch:
# the engine mints a research_reply task from it (see markers.py) and the
# heartbeat delivers actual results in a follow-up message.
_CHAT_INSTRUCTION = (
    "You are answering in a Telegram chat with your owner. Reply in short, natural "
    "messages. Lead with the answer and keep it brief. Never use an em dash; use a "
    "colon, a semicolon or a comma. Write the answer itself, never your reasoning "
    "about it.\n"
    "IMPORTANT: you have NO tools in this turn; no web access, no search, no "
    "files. Never claim to be searching or browsing, and never promise to report "
    "back on your own, you cannot. For questions that need LIVE or current "
    "information (news, prices, weather, releases), do not answer from memory: "
    "say in one short sentence that you'll look it up, and include this marker on "
    "its own line: [[research: <a concise web search query>]], the system runs "
    "the search and messages the results shortly. The query must be a CONCRETE "
    "search-engine query, never the user's vague phrase verbatim: name the topic, "
    "add specifics (product, place, month/year). 'dame las noticias de hoy' → "
    "[[research: technology news July 2026]], not [[research: noticias de hoy]]. "
    "At most one marker per reply.\n"
    "For an OPERATIONAL request that matches one of your registered actions "
    "(your live-configuration card lists them), include this marker on its own "
    "line: [[action: <name> key=value …]], the system mints a signed task, the "
    "allowlist and approval frontier apply, and the result arrives as a "
    "follow-up message. Never claim the action already ran, it runs after "
    "this reply. At most one action marker per reply. When the owner asks you "
    "to change one of your settings in plain language, that IS an operational "
    "request: [[action: set_config key=<setting> value=<new value>]], the "
    "editable keys are in your live-configuration card; quote a value that "
    'contains spaces (value="a, b").'
)

_MEMORY_NUDGE = (
    "If this conversation taught you a DURABLE fact about your owner (a "
    "preference, a project, a constraint, not small talk), append this marker "
    "at the very end: [[remember: <one concise fact>]]. At most one."
)


#: How much of the owner's message the episode keeps. Long enough to be worth
#: recalling, short enough that the log stays a log.
_QUESTION_CHARS = 400


def asked(text: str) -> str:
    """The owner's message, as a suffix for the episode's ``decision``.

    The chat episode stored only the ANSWER: ``accion`` is the constant
    ``"responder"`` and the question went nowhere, so ``/recall`` and the
    episodic-recall memory layer could never find a turn by what was ASKED, on
    the one door the owner uses every day. The question cannot ride in
    ``accion`` (``core/feedback.py``, ``security/autonomy.py`` and
    ``agents/repetition.py`` all key on that exact constant to tell a chat
    answer from a real action) nor in ``resultado`` (the re-ask detector
    compares answer text for near-duplicates). ``decision`` is free-form,
    FTS5-indexed, and only ever read with ``startswith``, so a suffix is the
    one place this fits without disturbing a reader.

    Square brackets are dropped: ``decision`` also carries ``[routable:x]``
    markers that ``repetition.py`` finds with a plain regex, and a quoted
    message must not be able to forge one.
    """
    flat = " ".join((text or "").split())[:_QUESTION_CHARS]
    flat = flat.replace("[", "(").replace("]", ")")
    return f" | q: {flat}" if flat else ""


async def brain_reply(text: str, *, config, send: Optional[Sender] = None) -> str:
    """Answer one message via a tool-less brain think. Never raises.

    Mirrors :func:`lhassa.voice.brain_bridge.answer`: load identity + layered memory
    (with ``text`` as the semantic query), let the router pick the routine
    provider+model (so the opencode heartbeat backend is preferred), call
    ``provider.complete`` with **no tools**, and log a ``despertar_por="telegram"``
    episode. ``send`` is how a no-approval ``[[action: ...]]`` reports back.
    Degrades to :data:`_FALLBACK_REPLY` on a missing provider / spent budget /
    any error; it never raises into the caller.
    """
    from lhassa.core import db, watchlist
    from lhassa.core.episode import Episode
    from lhassa.llm import router
    from lhassa.llm.gateway import BudgetExceeded, KillSwitchActive
    from lhassa.llm.provider import ProviderNotConfigured
    from lhassa.memory import answer_cache
    from lhassa.memory import load as memory_load
    from lhassa.notify.intent import classify
    from lhassa.notify import task_router
    from lhassa.persona import load_identity

    instance_id = getattr(config, "instance_id", None) or "inst_personal"

    # Idempotent: a chat turn must work even if it wakes the brain first.
    await db.init_db()

    ep = Episode.begin(despertar_por=CHAT_SOURCE, instance_id=instance_id)
    reply = _FALLBACK_REPLY
    try:
        # Plan 4, Stream 1: the versatile brain's three $0 axes (task type,
        # working mode, effort level), resolved BEFORE any spend. Strips a
        # `--<level>` marker (e.g. `--deep`) from the text so it never reaches
        # the model, the cache key, or the semantic memory query.
        routing = task_router.route(text)
        text = routing.text

        # $0 intent classification BEFORE any provider/spend is resolved (doc/concepts.md:
        # "thinking is the EXCEPTION"). Only a "cacheable" (stable, non-time-sensitive)
        # question is even eligible for the answer cache; live_data/judgment/command
        # are unaffected and fall straight through to the unchanged think below.
        intent = classify(text)
        cache_enabled = bool(getattr(config, "answer_cache_enabled", True))
        cache_stats: dict = {}
        if intent == "cacheable" and cache_enabled:
            threshold = float(getattr(config, "answer_cache_threshold", 0.90) or 0.90)
            hit = await answer_cache.lookup(
                instance_id, text, threshold=threshold, config=config,
                stats=cache_stats,
            )
            if hit is not None:
                cached_answer, similarity = hit
                # A cache hit is still part of the conversation: record it in the
                # rolling history exactly like a normal turn.
                state.history_record(config, "user", text)
                state.history_record(config, "assistant", cached_answer)
                ep.finish(
                    decision=(
                        f"{task_router.describe(routing)} | "
                        f"cache-hit: served past answer (sim={similarity:.2f})"
                        f"{asked(text)}"
                    ),
                    accion="responder",
                    resultado=cached_answer,
                    costo_usd=0.0,
                )
                return cached_answer

        # Layer memory (semantic query = the message; layer 4 fires only when
        # enabled + indexed, otherwise it's the unchanged pinned/recent/tags).
        ctx = await memory_load.load(
            instance_id,
            token_budget=getattr(config, "context_token_budget", 2000),
            query=text,
        )

        # Routine path: not delegated, not flagged hard → the router prefers the
        # local/opencode heartbeat provider (§3-Fase 6).
        trigger = watchlist.Trigger(kind="telegram", action="responder", params={})
        decision = router.decide(trigger, ctx, config=config)

        identity = load_identity(instance_id)
        parts = [identity.persona_block]
        memory_block = ctx.to_prompt()
        if memory_block:
            parts.append(memory_block)
        # The self-card: live config + execution-modality taxonomy, $0 and
        # deterministic: the model KNOWS what it runs on and how it executes
        # (and which action names the [[action: …]] marker may name).
        try:
            from lhassa.notify import selfcard as _selfcard

            parts.append(_selfcard.build(config))
        except Exception as exc:  # a broken card must not cost the reply
            logger.warning(f"chat: selfcard degraded: {exc}")
        parts.append(_CHAT_INSTRUCTION)

        # Rolling conversation history + the periodic memory nudge. The counter
        # increments per USER turn; on the cadence the model is invited (once)
        # to flag a durable owner fact with [[remember: …]].
        history = state.history_messages(config)
        turns = state.bump_turns(config)
        nudge_every = int(getattr(config, "chat_memory_nudge_turns", 6) or 6)
        if nudge_every > 0 and turns % nudge_every == 0:
            parts.append(_MEMORY_NUDGE)
        system = "\n\n".join(parts)
        messages = [*history, {"role": "user", "content": text}]

        # Try the routine provider (opencode/local, $0), then walk the configured
        # fallback CHAIN: the owner's CLI subscriptions (claude → codex) before the
        # cloud gateway (default). opencode boots a fresh runtime per call and flakes
        # under the long-running poller, so the chain backs it up; the owner always
        # gets an answer. The chain is the mockable ``_fallback_chain`` seam.
        #
        # ``telegram_provider`` pins the CHAT door's own provider (e.g. "default",
        # the fast API gateway) without touching the heartbeat seat that flows and
        # hard work think with; empty/"auto" keeps the router's routine decision.
        attempts: list[tuple] = []
        pinned = (getattr(config, "telegram_provider", "") or "").strip().lower()
        primary = decision.provider
        if pinned and pinned != "auto":
            try:
                from lhassa.llm.provider import get_model_provider

                primary = get_model_provider(config, name=pinned)
            except Exception as exc:  # a bad pin degrades to the routine decision
                logger.warning(
                    f"chat: telegram_provider {pinned!r} unresolvable "
                    f"({exc}), using the routine decision."
                )
                primary = decision.provider
        if primary is not None:
            model = (
                decision.model
                if primary is decision.provider
                else router.pick_model(primary, hard=False, config=config)
            )
            attempts.append((primary, model))
        attempts.extend(_fallback_chain(config, primary))
        if not attempts:
            raise ProviderNotConfigured("router resolved no provider for telegram turn")

        last_exc = None
        for prov, mdl in attempts:
            try:
                result = await prov.complete(
                    messages,
                    model=mdl,
                    # The declared effort budget governs the actual request:
                    # "minimal" and "deep" ask for visibly different caps, not
                    # just a different label after the fact.
                    max_tokens=routing.effort.token_budget,
                    system=system,
                )
            except KillSwitchActive as exc:
                # Manual shutdown is global: unlike a spent budget or a flaky
                # seat, it must not advance to the next configured provider.
                last_exc = exc
                break
            except (ProviderNotConfigured, BudgetExceeded) as exc:
                last_exc = exc
                continue
            except Exception as exc:  # a flaky provider must not end the turn
                logger.warning(
                    f"chat: provider {getattr(prov, 'name', '?')!r} failed: {exc}"
                )
                last_exc = exc
                continue
            reply = (result.text or "").strip()
            if not reply and (getattr(result, "output_tokens", 0) or 0) > 0:
                # The model ANSWERED but with empty content (reasoning ate the
                # cap; observed live with deepseek V4 Flash). Retry ONCE with
                # a larger cap on the same provider; "can't reach my model"
                # would be a lie here; the model was reached and billed.
                try:
                    result = await prov.complete(
                        messages,
                        model=mdl,
                        max_tokens=max(routing.effort.token_budget, _CHAT_MAX_TOKENS_RETRY),
                        system=system,
                    )
                    reply = (result.text or "").strip()
                except Exception as exc:
                    logger.warning(f"chat: empty-reply retry failed: {exc}")
                reply = reply or _EMPTY_REPLY
            reply = reply or _FALLBACK_REPLY
            reply = await markers.apply_research_marker(
                reply, instance_id=instance_id, config=config
            )
            reply = await markers.apply_action_marker(
                reply, instance_id=instance_id, config=config, send=send
            )
            reply = await markers.apply_remember_marker(reply, instance_id=instance_id)
            # The owner's no-em-dash rule, ENFORCED rather than requested. The
            # chat instruction above already asks for it and a model still ships
            # one, which is exactly why persona.scrub_dashes exists and why the
            # notify and digest passes run it. Applied here, after the markers
            # are stripped, so the scrubbed text is also what gets cached and
            # threaded into history: the rule holds on the reply the owner
            # actually reads, not just on the one the model meant to write.
            reply = _persona.scrub_dashes(reply)
            # Record the turn AS THE OWNER SAW IT (markers already stripped).
            state.history_record(config, "user", text)
            state.history_record(config, "assistant", reply)
            # A "cacheable" question just got a real think: store it so the NEXT
            # semantically-similar ask can be served for $0. Best-effort: a store
            # failure must never break a reply that already succeeded.
            if intent == "cacheable" and cache_enabled:
                with contextlib.suppress(Exception):
                    await answer_cache.store(instance_id, text, reply, config=config)
            # Record the provider that ACTUALLY answered: the router's reason
            # predates the telegram_provider pin and the fallback walk, and an
            # episode label that names the wrong provider poisons every later
            # read (spend analysis, the repetition detector, /recall).
            actual = getattr(prov, "name", "?")
            # The self-routing consciousness's marker: this turn actually spent
            # (a real think, and, via the [[research: …]] marker above, maybe
            # a minted research task too) on a message a deterministic
            # capability ALSO recognises but no route is on for yet. Tagging it
            # here is the router-side half of the seam; the consciousness
            # (agents.repetition.detect_routable) reads it back with plain SQL,
            # never a Python import; agents/ stays shell-free.
            from lhassa.notify import capabilities as _capabilities

            ep.finish(
                decision=(
                    f"{task_router.describe(routing)} | "
                    f"think: provider={actual} model={mdl or 'provider-default'}"
                    f"{_capabilities.tag_intent(text)}"
                    # The near-miss diagnostic (plan 0.2): when a cacheable
                    # question MISSED, record how close the cache came; days of
                    # these decide whether the fix is the threshold, traffic, or
                    # the embedder.
                    + (
                        f" cache-miss best={cache_stats['best_similarity']:.2f}"
                        f" scanned={cache_stats['scanned']}"
                        if cache_stats and not cache_stats.get("hit")
                        else ""
                    )
                    + asked(text)
                ),
                accion="responder",
                resultado=reply,
                costo_usd=result.cost_usd,
            )
            return reply

        # Every provider (routine + fallback chain) failed: defer, keep the episode.
        ep.finish(
            decision=_DEFERRED_DECISION,
            accion="responder",
            resultado=f"deferred: all providers failed ({last_exc})",
        )
        return _FALLBACK_REPLY

    except Exception as exc:  # never raise into the poll loop
        logger.warning(f"chat: brain think failed: {exc}")
        ep.finish(
            decision="telegram: error",
            accion="responder",
            resultado=f"error: {type(exc).__name__}: {exc}",
        )
        return _FALLBACK_REPLY
    finally:
        # Always log the episode; a logging failure must not break the turn.
        with contextlib.suppress(Exception):
            await ep.append()

