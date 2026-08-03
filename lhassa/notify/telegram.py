# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telegram notifier — a credential-gated adapter over the Telegram Bot API.

Mirrors the ``github_token`` discipline (§ the token-gated GitHub-remote hooks):
**no credential → no network, ever.** If either ``telegram_bot_token`` or
``telegram_chat_id`` is empty, :func:`send` returns ``False`` immediately without
touching the network. On any HTTP/transport error it logs and returns ``False`` —
it never raises, so a notification failure can never break a caller.

This is the adapter only; callers reach it through ``notify.dispatch.notify()``
(the watcher, actions, the scheduled digest).
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

# The Telegram Bot API send-message endpoint. ``<token>`` is interpolated per call.
_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"

_TIMEOUT = 15.0


def _credentials(config) -> tuple[str, str]:
    """Return ``(bot_token, chat_id)`` from a config object (defaults to empty)."""
    token = (getattr(config, "telegram_bot_token", "") or "").strip()
    chat_id = str(getattr(config, "telegram_chat_id", "") or "").strip()
    return token, chat_id


def is_configured(config) -> bool:
    """True only when BOTH a bot token and a chat id are present."""
    token, chat_id = _credentials(config)
    return bool(token and chat_id)


async def send(
    text: str,
    *,
    config=None,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[dict] = None,
) -> bool:
    """Send ``text`` to the configured Telegram chat. Returns whether it was sent.

    ``parse_mode`` (e.g. ``"HTML"``) and ``reply_markup`` (an inline-keyboard dict,
    passed straight through) are optional and default to ``None`` — omitted from the
    payload, so existing callers behave identically. A caller using
    ``parse_mode="HTML"`` is responsible for HTML-escaping any interpolated dynamic
    content (``html.escape``) before it reaches this function.

    Credential-gated: with no token or no chat id this returns ``False`` and makes
    **no network call**. Fully defensive: any HTTP error is logged and yields
    ``False``; this never raises.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    token, chat_id = _credentials(config)
    if not token or not chat_id:
        # No credentials → never touch the network (the github_token pattern).
        return False

    import httpx

    url = _SEND_URL.format(token=token)
    payload: dict = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=payload)
    except Exception as exc:  # transport error — never propagate
        logger.warning(f"Telegram notify failed (transport): {exc}")
        return False

    if response.status_code != 200:
        logger.warning(
            f"Telegram notify failed: HTTP {response.status_code} {response.text[:200]}"
        )
        return False

    try:
        data = response.json()
    except Exception:
        logger.warning("Telegram notify: could not decode response JSON")
        return False

    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(f"Telegram notify rejected: {str(data)[:200]}")
        return False

    return True
