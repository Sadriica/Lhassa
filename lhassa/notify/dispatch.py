"""Notification dispatcher — the single fan-out entry point for outbound alerts.

Other modules call :func:`notify` rather than poking a channel adapter directly.
It fans out over the **modular channel registry** (:mod:`lhassa.notify.channels`):
``config.notify_channels`` names the channels to use, in order, and each registered
+ configured one receives the message. New channels are drop-in (a registered
module), never an edit here.

Discipline mirrors the channels it drives:
- **credential-gated** — an unconfigured channel is skipped (no network/no toast);
- **never raises** — a channel failure is logged and swallowed, so a notification
  can never break the caller.

``level`` is an advisory severity ("info" / "warning" / "error" / …) passed to each
channel (which may use it for urgency/formatting); a channel is free to ignore it.
"""

from __future__ import annotations

from loguru import logger

from lhassa.notify.channels import active_channels


async def notify(text: str, *, level: str = "info", config=None) -> bool:
    """Fan ``text`` out to every configured notification channel.

    Returns ``True`` if at least one channel delivered the message, ``False`` if
    none did (nothing configured, or every channel declined/failed). Fully
    defensive: a channel raising is logged and treated as a non-delivery; this
    never propagates.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    delivered = False
    for channel in active_channels(config):
        try:
            if await channel.send(text, level=level, config=config):
                delivered = True
        except Exception as exc:  # a channel must never break the dispatcher
            name = getattr(channel, "name", "?")
            logger.warning(f"notify: channel {name!r} raised: {exc}")

    return delivered
