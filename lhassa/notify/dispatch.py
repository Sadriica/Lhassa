"""Notification dispatcher — the single fan-out entry point for outbound alerts.

Other modules (later) call :func:`notify` rather than poking a channel adapter
directly. Today the only channel is Telegram (:mod:`lhassa.notify.telegram`); more
channels can be added behind the same shape without touching callers.

Discipline mirrors the adapters it wraps:
- **credential-gated** — an unconfigured channel is a silent no-op (no network);
- **never raises** — a channel failure is logged and swallowed, so a notification
  can never break the caller.

``level`` is an advisory severity ("info" / "warning" / "error" / …). It is kept
on the message and reserved for future per-channel routing/filtering; today every
configured channel receives every message regardless of level.
"""

from __future__ import annotations

from loguru import logger

from lhassa.notify import telegram


async def notify(text: str, *, level: str = "info", config=None) -> bool:
    """Fan ``text`` out to every configured notification channel.

    Returns ``True`` if at least one channel accepted the message, ``False`` if
    none did (nothing configured, or every channel declined/failed). Fully
    defensive: a channel raising is logged and treated as a non-delivery; this
    never propagates.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    delivered = False

    # Telegram — the existing credential-gated adapter. ``send`` already no-ops
    # (returns False, no network) when unconfigured, but gate here too so an
    # unconfigured box never even constructs the call.
    if telegram.is_configured(config):
        try:
            if await telegram.send(text, config=config):
                delivered = True
        except Exception as exc:  # an adapter must never break the dispatcher
            logger.warning(f"notify: telegram channel raised: {exc}")

    return delivered
