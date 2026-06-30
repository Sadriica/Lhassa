"""Inbound Telegram poller — turns Lhassa's Telegram chat into a command channel.

Long-polls the Telegram Bot API ``getUpdates`` (offset-based) over httpx, answers
each accepted message through the brain's one-turn path
(:func:`lhassa.cli.agent.run_once`), and replies via the existing outbound
:func:`lhassa.notify.telegram.send`.

Same discipline as the rest of :mod:`lhassa.notify`:
- **gated** — runs only when ``config.telegram_poll_enabled`` is true AND both a
  bot token and chat id are configured; otherwise it is a silent, network-free
  no-op (logged once);
- **owner-only** — messages whose ``chat.id`` is not exactly ``telegram_chat_id``
  are ignored (security: the bot answers nobody but its owner);
- **never raises** — a bad/malformed update is logged and skipped; a single error
  can never kill the poll loop.

Run standalone with ``python -m lhassa.notify.telegram_inbound``. It is **not**
wired into ``main.py`` — that is a later step.
"""

from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from lhassa.notify import telegram

# The getUpdates endpoint. ``<token>`` is interpolated per call.
_GET_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"

# Long-poll seconds passed to Telegram; the client timeout sits a little above it.
_LONG_POLL_SECONDS = 30
_TIMEOUT = float(_LONG_POLL_SECONDS) + 15.0

# Backoff bounds for run_forever when a cycle errors out (seconds).
_BACKOFF_START = 1.0
_BACKOFF_MAX = 60.0


def _enabled(config) -> bool:
    """True only when polling is switched on AND credentials are present."""
    return bool(getattr(config, "telegram_poll_enabled", False)) and telegram.is_configured(config)


async def _answer(text: str, *, config) -> None:
    """Run one message through the brain and reply with the result. Never raises."""
    try:
        from lhassa.cli.agent import run_once

        result = await run_once(text, config=config)
    except Exception as exc:
        logger.warning(f"Telegram inbound: run_once failed: {exc}")
        # Best-effort apology; still never raise.
        try:
            await telegram.send("Sorry — I hit an error handling that.", config=config)
        except Exception:
            pass
        return

    reply = getattr(result, "response", None) or str(result)
    try:
        await telegram.send(reply, config=config)
    except Exception as exc:
        logger.warning(f"Telegram inbound: reply send failed: {exc}")


async def poll_once(config=None, *, offset=None):
    """Run one ``getUpdates`` cycle. Returns the next offset to use.

    Gated + defensive: when not enabled/configured it returns ``offset`` unchanged
    and makes **no network call**. Accepts only messages from the configured chat
    id, answers each through the brain, and advances the offset past every update
    seen (so even ignored updates are not re-fetched). Never raises.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    if not _enabled(config):
        return offset

    token = (getattr(config, "telegram_bot_token", "") or "").strip()
    allowed_chat = str(getattr(config, "telegram_chat_id", "") or "").strip()
    url = _GET_UPDATES_URL.format(token=token)
    params: dict = {"timeout": _LONG_POLL_SECONDS}
    if offset is not None:
        params["offset"] = offset

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, params=params)
    except Exception as exc:  # transport error — keep the offset, let caller retry
        logger.warning(f"Telegram inbound: getUpdates failed (transport): {exc}")
        return offset

    if response.status_code != 200:
        logger.warning(
            f"Telegram inbound: getUpdates HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
        return offset

    try:
        data = response.json()
    except Exception:
        logger.warning("Telegram inbound: could not decode getUpdates JSON")
        return offset

    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(f"Telegram inbound: getUpdates rejected: {str(data)[:200]}")
        return offset

    updates = data.get("result") or []
    new_offset = offset
    for update in updates:
        # Advance past every update we see, even malformed/ignored ones, so the
        # next cycle does not re-fetch them.
        try:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                new_offset = update_id + 1

            message = update.get("message") or update.get("edited_message")
            if not isinstance(message, dict):
                continue

            chat_id = str((message.get("chat") or {}).get("id", "")).strip()
            if chat_id != allowed_chat:
                # Security: answer nobody but the owner.
                logger.info(f"Telegram inbound: ignoring message from chat {chat_id!r}")
                continue

            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                continue

            await _answer(text.strip(), config=config)
        except Exception as exc:  # one bad update must not kill the loop
            logger.warning(f"Telegram inbound: skipping bad update: {exc}")
            continue

    return new_offset


async def run_forever(config=None) -> None:
    """Poll forever, calling :func:`poll_once` with exponential backoff on error.

    When polling is disabled or unconfigured this logs once and returns
    immediately — no network, no loop.
    """
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
    offset = None
    backoff = _BACKOFF_START
    while True:
        try:
            new_offset = await poll_once(config=config, offset=offset)
            if new_offset != offset:
                offset = new_offset
            backoff = _BACKOFF_START  # a clean cycle resets the backoff
        except Exception as exc:  # poll_once is defensive, but belt-and-braces
            logger.warning(f"Telegram inbound: poll cycle error: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


if __name__ == "__main__":
    asyncio.run(run_forever())
