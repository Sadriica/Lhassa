# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inbound Telegram poller: the Telegram transport of the chat engine.

Long-polls the Telegram Bot API ``getUpdates`` (offset-based) over httpx and
turns each accepted update into a :class:`lhassa.chat.InboundMessage` for
:func:`lhassa.chat.respond`, which answers it (slash commands, shortcuts, the
tool-less brain think, ...) and replies through this module's sender
(:func:`lhassa.notify.telegram.send`, with the reply's buttons drawn as an
inline keyboard). Everything about WHAT to answer lives in :mod:`lhassa.chat`;
this module only knows the wire:

- **gated**: runs only when ``config.telegram_poll_enabled`` is true AND both a
  bot token and chat id are configured; otherwise it is a silent, network-free
  no-op (logged once);
- **owner-only**: messages (and button taps) whose ``chat.id`` is not exactly
  ``telegram_chat_id`` are ignored (security: the bot answers nobody but its
  owner);
- **never raises**: a bad/malformed update is logged and skipped; a single error
  can never kill the poll loop.

Run standalone with ``python -m lhassa.notify.telegram_inbound`` (the
``lhassa-telegram`` systemd unit runs exactly that); it is deliberately a
separate process from ``main.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from typing import Optional

import httpx
from loguru import logger

from lhassa import chat
from lhassa.chat.types import InboundDocument, InboundMessage, Reply
from lhassa.notify import commands, telegram

# The getUpdates endpoint. ``<token>`` is interpolated per call.
_GET_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"

# Long-poll seconds passed to Telegram; the client timeout sits a little above it.
_LONG_POLL_SECONDS = 30
_TIMEOUT = float(_LONG_POLL_SECONDS) + 15.0

# Backoff bounds for run_forever when a cycle errors out (seconds).
_BACKOFF_START = 1.0
_BACKOFF_MAX = 60.0

# Process-local guard against Telegram redelivering an ``update_id`` we already
# dispatched. The offset param sent on the NEXT getUpdates call is the only
# thing that tells Telegram "stop sending me this"; Telegram's own docs warn
# that updates already delivered may still show up again "for a short period
# of time" around an offset change, and this poller's ``offset`` is an
# in-memory variable in ``run_forever`` (never persisted to disk), so it is
# also lost on a crash/restart between "processed the batch" and "sent the
# advanced offset back". Either way the same update can reach this loop twice.
# A bounded, process-local seen-set is the cheap fix: it cannot survive a
# restart (the poller is a single process, and that is the documented scope),
# but it stops the two realistic single-process cases: a resend racing the
# offset ack, and a retried cycle after a transient local error that still
# landed the previous batch. Bounded so it cannot grow across a long-lived
# process.
_SEEN_UPDATE_IDS_MAXLEN = 2048
_seen_update_ids: "deque[int]" = deque(maxlen=_SEEN_UPDATE_IDS_MAXLEN)
_seen_update_ids_set: set[int] = set()

def _mark_update_seen(update_id: int) -> bool:
    """Record ``update_id`` as processed; return True if it was already seen.

    Bounded FIFO: once the deque evicts an old id past ``maxlen``, it is
    dropped from the set too, so memory never grows unbounded. Never raises.
    """
    if update_id in _seen_update_ids_set:
        return True
    if len(_seen_update_ids) >= _SEEN_UPDATE_IDS_MAXLEN:
        oldest = _seen_update_ids[0]
        _seen_update_ids_set.discard(oldest)
    _seen_update_ids.append(update_id)
    _seen_update_ids_set.add(update_id)
    return False


def reset_seen_updates() -> None:
    """Forget every processed ``update_id``.

    Process-local state outlives a single poll, which is the whole point in
    production and a hazard anywhere a fresh poller is expected: two runs in one
    process (the test suite, a REPL) would silently drop the second one's
    updates as duplicates. The suite calls this between tests.
    """
    _seen_update_ids.clear()
    _seen_update_ids_set.clear()


def reset_conversation_state() -> None:
    """Forget the chat engine's in-memory conversation state (see
    :func:`lhassa.chat.reset_conversation_state`); kept here for callers that
    reset "the poller"."""
    chat.reset_conversation_state()


def _enabled(config) -> bool:
    """True only when polling is switched on AND credentials are present."""
    return bool(getattr(config, "telegram_poll_enabled", False)) and telegram.is_configured(config)


def _inline_keyboard(buttons) -> Optional[dict]:
    """Draw a reply's buttons as one inline-keyboard row (``None`` if none)."""
    if not buttons:
        return None
    return {
        "inline_keyboard": [
            [{"text": b.label, "callback_data": b.data} for b in buttons]
        ]
    }


def _sender(config):
    """The chat engine's :data:`~lhassa.chat.types.Sender` for this bot."""

    async def _send(reply: Reply) -> bool:
        keyboard = _inline_keyboard(reply.buttons)
        if keyboard is not None:
            return await telegram.send(reply.text, config=config, reply_markup=keyboard)
        return await telegram.send(reply.text, config=config)

    return _send


def _fetcher(file_id: str, config):
    async def _fetch() -> Optional[bytes]:
        from lhassa.notify import telegram_api

        return await telegram_api.get_file(file_id, config=config)

    return _fetch


def _acknowledger(callback_id: str, config):
    async def _ack(text: str) -> bool:
        if not callback_id:
            return False
        from lhassa.notify import telegram_api

        return await telegram_api.answer_callback_query(callback_id, text=text, config=config)

    return _ack


async def _handle_callback_query(callback_query: dict, *, config, allowed_chat: str) -> None:
    """Handle an inline-keyboard tap (Approve/Deny). Never raises.

    The owner check is the transport's: a tap from any chat but the owner's is
    acknowledged (so the tapper's spinner clears) and otherwise ignored. An
    owner tap goes to the chat engine, which resolves the approval and answers
    with the ack; :func:`lhassa.chat.respond` acknowledges the button
    (``answerCallbackQuery``), posts the ack in the chat and starts whatever
    the approval held back.
    """
    callback_id = str(callback_query.get("id") or "") if isinstance(callback_query, dict) else ""
    ack = _acknowledger(callback_id, config)
    try:
        message = callback_query.get("message") or {}
        chat_id = (
            str((message.get("chat") or {}).get("id", "")).strip()
            if isinstance(message, dict)
            else ""
        )
        if chat_id and chat_id != allowed_chat:
            # Security: honor a tap from nobody but the owner's chat.
            logger.info(f"Telegram inbound: ignoring callback from chat {chat_id!r}")
            await ack("")
            return

        await chat.respond(
            InboundMessage(
                chat_id=chat_id or allowed_chat,
                callback=str(callback_query.get("data") or ""),
                send=_sender(config),
                acknowledge=ack,
            ),
            config=config,
        )
    except Exception as exc:  # a malformed callback_query must never crash the poller
        logger.warning(f"Telegram inbound: callback_query handling failed: {exc}")
        with contextlib.suppress(Exception):
            await ack("")


async def _answer(text: str, *, config, chat_id: str = "") -> None:
    """Answer one text message and reply with it. Never raises."""
    await chat.respond(
        InboundMessage(chat_id=chat_id, text=text, send=_sender(config)), config=config
    )


async def _answer_document(message: dict, *, config, chat_id: str) -> None:
    """Hand an uploaded document to the chat engine's document hooks."""
    document = message.get("document") or {}
    caption = message.get("caption") or message.get("text")
    file_id = str(document.get("file_id") or "")
    await chat.respond(
        InboundMessage(
            chat_id=chat_id,
            document=InboundDocument(
                file_id=file_id,
                file_name=str(document.get("file_name") or ""),
                caption=caption,
                fetch=_fetcher(file_id, config),
                raw=dict(document),
            ),
            send=_sender(config),
        ),
        config=config,
    )


async def poll_once(config=None, *, offset=None):
    """Run one ``getUpdates`` cycle. Returns the next offset to use.

    Gated + defensive: when not enabled/configured it returns ``offset`` unchanged
    and makes **no network call**. Accepts only messages from the configured chat
    id, answers each through the brain, and advances the offset past every update
    seen (so even ignored updates are not re-fetched). Never raises.

    This is the OFFSET-ONLY view, kept for callers that only care about the
    cursor. It cannot distinguish a failed cycle from an idle one (both return
    ``offset`` unchanged), so a polling LOOP must use :func:`poll_cycle`, which
    reports success separately. See that function for why.
    """
    cycle_offset, _ok = await poll_cycle(config=config, offset=offset)
    return cycle_offset


async def poll_cycle(config=None, *, offset=None):
    """One ``getUpdates`` cycle as ``(next_offset, ok)``.

    ``ok`` is False when the cycle failed (transport error or a non-200), True
    otherwise, INCLUDING a clean long-poll that simply saw no updates.

    This distinction is the whole point. :func:`poll_once` returns ``offset``
    unchanged both when the network is down and when nothing happened, so a
    loop built on it cannot tell "broken" from "idle" and spins as fast as the
    failures come back. With DNS down that measured ~45 getUpdates attempts per
    second: a laptop burning CPU and battery and flooding the journal, while
    ``run_forever``'s exponential backoff sat unused because ``poll_once``
    never raised. Returning ``ok`` is what lets the loop actually back off.

    Never raises.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    if not _enabled(config):
        return offset, True  # disabled is not a failure: nothing to back off from

    token = (getattr(config, "telegram_bot_token", "") or "").strip()
    allowed_chat = str(getattr(config, "telegram_chat_id", "") or "").strip()
    url = _GET_UPDATES_URL.format(token=token)
    params: dict = {"timeout": _LONG_POLL_SECONDS}
    if offset is not None:
        params["offset"] = offset

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, params=params)
    except Exception as exc:  # transport error: keep the offset, let caller retry
        logger.warning(f"Telegram inbound: getUpdates failed (transport): {exc}")
        return offset, False

    if response.status_code != 200:
        logger.warning(
            f"Telegram inbound: getUpdates HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
        return offset, False

    try:
        data = response.json()
    except Exception:
        logger.warning("Telegram inbound: could not decode getUpdates JSON")
        return offset, False

    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(f"Telegram inbound: getUpdates rejected: {str(data)[:200]}")
        return offset, False

    updates = data.get("result") or []
    new_offset = offset
    for update in updates:
        # Advance past every update we see, even malformed/ignored ones, so the
        # next cycle does not re-fetch them.
        try:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                new_offset = update_id + 1
                # See ``_mark_update_seen`` above: Telegram may resend an
                # update_id we already dispatched (offset ack race or a
                # process restart between processing and confirming). Any
                # dispatch path below (callback, marker-minting think,
                # /goal, /remind, /idea) would otherwise re-mint on a
                # replay, so the guard sits before every one of them.
                if _mark_update_seen(update_id):
                    logger.info(
                        f"Telegram inbound: skipping duplicate update_id={update_id}"
                    )
                    continue

            # An inline-keyboard button tap (Approve/Deny on a monitor alert).
            callback_query = update.get("callback_query")
            if isinstance(callback_query, dict):
                await _handle_callback_query(callback_query, config=config, allowed_chat=allowed_chat)
                continue

            message = update.get("message") or update.get("edited_message")
            if not isinstance(message, dict):
                continue

            chat_id = str((message.get("chat") or {}).get("id", "")).strip()
            if chat_id != allowed_chat:
                # Security: answer nobody but the owner.
                logger.info(f"Telegram inbound: ignoring message from chat {chat_id!r}")
                continue

            # An uploaded document goes to the inbound document hooks (the cv
            # pack's CV bridge, when installed); with no hook that takes it, the
            # document is skipped. A plain text message keeps the brain path.
            document = message.get("document")
            if isinstance(document, dict) and document.get("file_id"):
                await _answer_document(message, config=config, chat_id=chat_id)
                continue

            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                continue

            await _answer(text.strip(), config=config, chat_id=chat_id)
        except Exception as exc:  # one bad update must not kill the loop
            logger.warning(f"Telegram inbound: skipping bad update: {exc}")
            continue

    return new_offset, True


async def run_forever(config=None) -> None:
    """Poll forever, calling :func:`poll_once` with exponential backoff on error.

    When polling is disabled or unconfigured this logs once and returns
    immediately: no network, no loop.
    """
    from lhassa import actionpack
    actionpack.register_all()

    if config is None:
        from lhassa.config import Config

        config = Config()

    if not _enabled(config):
        logger.info(
            "Telegram inbound poller not started "
            "(telegram_poll_enabled off or token/chat id missing)."
        )
        return

    logger.info("Telegram inbound poller started.")

    # Best-effort: publish the bot's command menu once on start. Guarded so a slow or
    # failing setMyCommands can never block or crash the poller's startup path.
    with contextlib.suppress(Exception):
        from lhassa.notify import telegram_api

        # The menu IS the registry: every registered command (described first,
        # curated order; commands.menu_entries shares /help's one source), plus
        # /cv, the bridge command handled ahead of the registry in _answer, so
        # it is not in COMMANDS but very much answers.
        menu = [("cv", "Tailor your CV to a job posting")]
        menu.extend(commands.menu_entries())
        await telegram_api.set_my_commands(menu, config=config)

    offset = None
    backoff = _BACKOFF_START
    while True:
        try:
            new_offset, ok = await poll_cycle(config=config, offset=offset)
            if new_offset != offset:
                offset = new_offset
            if ok:
                backoff = _BACKOFF_START  # a clean cycle resets the backoff
            else:
                # poll_cycle already logged WHY. Without this sleep the loop
                # spins at the speed of the failure (measured ~45 attempts/s
                # with DNS down); the backoff below existed but could never
                # fire, because poll_once swallows transport errors instead of
                # raising. Sleep BEFORE growing it so the first retry is quick.
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
        except Exception as exc:  # poll_cycle is defensive, but belt-and-braces
            logger.warning(f"Telegram inbound: poll cycle error: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


if __name__ == "__main__":
    asyncio.run(run_forever())

