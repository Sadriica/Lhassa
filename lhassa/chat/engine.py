# SPDX-License-Identifier: AGPL-3.0-or-later
"""The chat engine: one owner message in, what to say back out.

Transport-agnostic. A transport builds an
:class:`~lhassa.chat.types.InboundMessage` and calls :func:`handle` (returns
the :class:`~lhassa.chat.types.Reply`, sends nothing itself) or
:func:`respond` (handles, then acknowledges/sends/starts follow-ups through the
message's own ``acknowledge`` / ``send``). Neither raises.

Order of handling for a text message (each step that answers ends the turn):

  1. inbound text hooks (:mod:`lhassa.notify.inbound_hooks`): a pack that
     replies with more than a string (the cv pack's ``/cv``);
  2. a reply to a pending personal check-in prompt;
  3. the natural "acuerdame/remind me ... <time>" short-circuit to ``/remind``;
  4. capability shortcuts (:mod:`lhassa.notify.capabilities`, $0);
  5. the deterministic "remember: X" fact write;
  6. an approved capability route;
  7. a follow-up that continues the sticky topic;
  8. watch rules (mint a task);
  9. the slash-command registry (LLM-free), with the ``/code`` buttons;
 10. the tool-less brain think (:mod:`lhassa.chat.think`).
"""

from __future__ import annotations

import contextlib
import re
from typing import Optional

from loguru import logger

from lhassa.chat import callbacks, rules, state, think
from lhassa.chat.outbound import resolve_sender, text_sender
from lhassa.chat.types import CHAT_SOURCE, InboundMessage, Reply
from lhassa.notify import commands, inbound_hooks

__all__ = ["handle", "respond", "answer_text"]

# Natural "remind me" trigger: routed to the /remind parser when a time phrase
# is present ("acuerdame de X a las 5pm"); time-less phrasings fall through.
_REMIND_NL_RE = re.compile(
    r"^(acu[ée]rdame|recu[ée]rdame|remind me)\b[,:]?\s*", re.IGNORECASE
)

# Deterministic remember command: "remember: X", "recuerda: X", "recordá: X".
# Handled BEFORE the watch-rules hook so it is $0 (no LLM, no task mint).
_REMEMBER_RE = re.compile(
    r'^(remember|recuerda|record[áa])[,:]?\s+(.+)',
    re.IGNORECASE | re.DOTALL,
)

# The hidden marker /code appends to a successful park reply (see
# lhassa.notify.commands._CODE_APPROVAL_MARKER): parsed here to attach the
# Approve/Deny buttons, then stripped so the owner never sees it.
_CODE_APPROVAL_RE = re.compile(r"\[\[\s*approval_id\s*:\s*(\S+?)\s*\]\]")


def _instance(config) -> str:
    return getattr(config, "instance_id", None) or "inst_personal"


async def _log_capability_episode(config, *, decision: str, accion: str, resultado: str) -> None:
    """One $0 episode for a deterministic capability answer. Never raises."""
    try:
        from lhassa.core import db as _db
        from lhassa.core.episode import Episode

        await _db.init_db()
        ep = Episode.begin(despertar_por=CHAT_SOURCE, instance_id=_instance(config))
        ep.finish(decision=decision, accion=accion, resultado=resultado, costo_usd=0.0)
        await ep.append()
    except Exception as exc:  # a logging failure must not swallow a good reply
        logger.warning(f"chat: {accion} episode logging failed: {exc}")


async def _answer_capability_shortcut(text: str, *, config):
    """Serve ``text`` through the first catalog capability whose always-on
    ``shortcut`` answers it ($0, deterministic; the calendar pack's
    ``calendar_view`` is the one today). Logs a ``despertar_por="telegram"``
    episode (``accion=<capability>``, the decision the shortcut names) and
    records the turn in chat history exactly like a normal reply.

    Returns ``(capability, reply)``, or ``None`` when no capability answers
    (including when no pack registered one). Never raises.
    """
    from lhassa.notify import capabilities as _capabilities

    for cap in list(_capabilities.CAPABILITIES):
        if cap.shortcut is None:
            continue
        try:
            hit = await cap.shortcut(text, config=config)
        except Exception as exc:
            logger.warning(f"chat: {cap.name} shortcut failed: {exc}")
            continue
        if not hit:
            continue
        reply, decision = hit
        if reply is None:
            continue
        await _log_capability_episode(
            config, decision=decision, accion=cap.name, resultado=reply
        )
        state.history_record(config, "user", text)
        state.history_record(config, "assistant", reply)
        return cap, reply
    return None


async def _checkin_reply(stripped: str, *, config) -> Optional[Reply]:
    """Capture a plain reply to a pending personal check-in prompt, if any."""
    try:
        from lhassa.core import db as _db_prompt
        from lhassa.memory import checkins as _checkins

        instance_id = _instance(config)
        await _db_prompt.init_db()
        prompt = await _db_prompt.pending_owner_prompt(instance_id, kind="personal_checkin")
        if prompt:
            item = await _checkins.record(
                instance_id,
                stripped,
                slot=prompt.get("slot") or None,
                source="telegram-checkin-reply",
            )
            await _db_prompt.consume_owner_prompt(
                instance_id, prompt["id"], answer=stripped[:1000]
            )
            return Reply(text=f"Noted for the day: {item.id}")
    except Exception as exc:
        logger.warning(f"chat: check-in reply capture failed: {exc}")
    return None


async def _remember(stripped: str, match, *, config) -> Reply:
    text_to_remember = match.group(2).strip()[:500]
    try:
        from lhassa.core import db as _db_remember
        from lhassa.memory.facts import add_fact as _add_fact_remember

        await _db_remember.init_db()
        fact = await _add_fact_remember(
            _instance(config),
            tipo="dato",
            contenido=text_to_remember,
            procedencia="telegram-remember",
            confianza=0.95,
            tags=["telegram"],
        )
        return Reply(text=f"Remembered: {fact.id}")
    except Exception as exc:
        logger.warning(f"chat: remember failed: {exc}")
        return Reply(text="Failed to remember that.")


async def _continue_topic(stripped: str, *, config, chat_id: str) -> Optional[str]:
    """Continue the sticky topic for a connector-led follow-up, or ``None``.

    $0, deterministic: only fires when (a) the message reads as a follow-up
    (capabilities.is_followup), (b) a fresh topic exists, and (c) that
    capability declares a continue_followup. Any "no" (including the
    capability's own continuation declining with None) falls through.
    """
    from lhassa.notify import capabilities as _capabilities

    if not _capabilities.is_followup(stripped):
        return None
    topic = state.topic_get(config, chat_id)
    if topic is None:
        return None
    cap = _capabilities.get_capability(topic.get("capability", ""))
    if cap is None or cap.continue_followup is None:
        return None
    try:
        followup_reply = await cap.continue_followup(
            stripped, topic.get("context") or {}, config=config
        )
    except Exception as exc:
        logger.warning(f"chat: thread-continue {cap.name!r} failed: {exc}")
        followup_reply = None
    if followup_reply is None:
        return None
    await _log_capability_episode(
        config,
        decision=f"thread-continue: {cap.name}",
        accion=cap.name,
        resultado=followup_reply,
    )
    state.history_record(config, "user", stripped)
    state.history_record(config, "assistant", followup_reply)
    # Keep the sticky topic, refreshing its context if THIS follow-up moved it
    # (the calendar day: "y el sabado?") so the next bare-time follow-up
    # targets the right day. A falsy refreshed value never clobbers.
    new_ctx = dict(topic.get("context") or {})
    moved = _capabilities.topic_context(cap, stripped)
    new_ctx.update({k: v for k, v in moved.items() if v})
    state.topic_set(config, chat_id, cap.name, new_ctx)
    return followup_reply


async def answer_text(message: InboundMessage, *, config) -> Optional[Reply]:
    """Answer one text message. ``None`` means a hook already replied itself."""
    text = message.text
    chat_id = message.chat_id
    send = resolve_sender(message.send, config)
    stripped = text.strip()

    if await inbound_hooks.run_text_hooks(
        text, config=config, chat_id=chat_id, reply=text_sender(send)
    ):
        return None

    plain = not stripped.startswith("/")

    if plain and getattr(config, "digest_personal_checkin", False):
        checkin = await _checkin_reply(stripped, config=config)
        if checkin is not None:
            return checkin

    command_name, _ = commands.parse(stripped)

    # "acuerdame de un compromiso a las 5pm" routes straight to the /remind
    # parser ($0, no LLM), only when a TIME phrase parses; a time-less
    # "acuerdame que..." falls through to the remember/fact paths below.
    if plain:
        m_remind = _REMIND_NL_RE.match(stripped)
        if m_remind:
            rest = stripped[m_remind.end():].strip()
            if commands.parse_remind(rest) is not None:
                handler = commands.COMMANDS.get_or_none("remind")
                if handler is not None:
                    return Reply(text=await handler(rest, config=config))

    if plain:
        # Deterministic capability shortcuts ("what do I have tomorrow?" from the
        # calendar pack): $0, NO model, never the [[research:]] bridge. With no
        # pack installed the catalog is empty and this is a no-op.
        shortcut_hit = await _answer_capability_shortcut(stripped, config=config)
        if shortcut_hit is not None:
            from lhassa.notify import capabilities as _capabilities

            hit_cap, shortcut_reply = shortcut_hit
            # This turn IS the capability's topic now; the topic context (the
            # calendar's DAY) lets a bare-time follow-up resolve against it.
            state.topic_set(
                config, chat_id, hit_cap.name, _capabilities.topic_context(hit_cap, stripped)
            )
            return Reply(text=shortcut_reply)

        m = _REMEMBER_RE.match(stripped)
        if m:
            return await _remember(stripped, m, config=config)

        # An APPROVED capability route (the self-routing consciousness) is
        # served here for $0, before the command/brain fallback.
        from lhassa.notify import capabilities as _capabilities

        matched_cap = _capabilities.match(stripped)
        routed_reply = await _capabilities.apply_route(stripped, config=config)
        if routed_reply is not None:
            if matched_cap:
                state.topic_set(config, chat_id, matched_cap)
            return Reply(text=routed_reply)

        followup = await _continue_topic(stripped, config=config, chat_id=chat_id)
        if followup is not None:
            return Reply(text=followup)

        # Watch rules: a match mints a task and confirms with its id.
        watch = rules.load_watch_rules(config)
        if watch:
            task_id = await rules.try_mint_rule_task(stripped, watch, config=config)
            if task_id:
                return Reply(text=f"Noted. Task {task_id} minted.")

    reply = None
    try:
        reply = await commands.dispatch(text, config=config)
    except Exception as exc:  # dispatch is defensive, but belt-and-braces
        logger.warning(f"chat: command dispatch failed: {exc}")
        reply = None

    if reply is None:
        # Not a command: let the brain answer (the plain-language path).
        return Reply(text=await think.brain_reply(text, config=config, send=message.send))

    if command_name == "code":
        # A successful /code park carries a hidden marker with the fresh
        # approval id: attach the Approve/Deny buttons and strip the marker.
        m = _CODE_APPROVAL_RE.search(reply)
        if m:
            visible = _CODE_APPROVAL_RE.sub("", reply).strip()
            return Reply(text=visible, buttons=callbacks.approval_buttons(m.group(1)))

    return Reply(text=reply)


async def _answer_document(message: InboundMessage, *, config) -> None:
    document = message.document
    send = resolve_sender(message.send, config)
    handled = await inbound_hooks.run_document_hooks(
        document.as_dict(),
        document.caption,
        config=config,
        chat_id=message.chat_id,
        reply=text_sender(send),
        fetch=document.fetch,
    )
    if not handled:
        logger.info("chat: no hook handled an uploaded document")


async def handle(message: InboundMessage, *, config) -> Optional[Reply]:
    """Turn one inbound event into the reply to send. Never raises.

    A tap (``message.callback``) resolves an approval
    (:func:`lhassa.chat.callbacks.handle_callback`); a document goes to the
    inbound document hooks (they reply themselves, so the result is
    ``None``); anything else is a text turn (:func:`answer_text`).
    """
    try:
        if message.callback is not None:
            return await callbacks.handle_callback(
                message.callback, config=config, send=message.send
            )
        if message.document is not None:
            await _answer_document(message, config=config)
            return None
        if not (message.text or "").strip():
            return None
        return await answer_text(message, config=config)
    except Exception as exc:  # the door never raises into its transport
        logger.warning(f"chat: handling failed: {exc}")
        return None


async def respond(message: InboundMessage, *, config) -> Optional[Reply]:
    """:func:`handle`, then play the reply through the message's transport.

    In order: acknowledge a tap (``message.acknowledge(reply.ack)``), send the
    reply's text (``message.send``, or the channel fallback), then start its
    follow-ups. Returns the reply. Never raises.
    """
    reply = await handle(message, config=config)
    if reply is None:
        return None
    if reply.ack is not None and message.acknowledge is not None:
        with contextlib.suppress(Exception):
            await message.acknowledge(reply.ack)
    if reply.text:
        try:
            await resolve_sender(message.send, config)(reply)
        except Exception as exc:
            logger.warning(f"chat: reply send failed: {exc}")
    callbacks.start_followups(reply)
    return reply
