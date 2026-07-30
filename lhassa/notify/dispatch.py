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

Before fan-out, ``text`` passes through :func:`lhassa.notify.voice.revoice` — the
brain's own voice layer (``config.notify_style``: plain / template / persona) that
reshapes machine-built lines into something a person would actually read. Voicing
is itself defensive: any failure there degrades to the original ``text``, so a
notification always arrives even if the voice layer breaks.

``level`` is an advisory severity ("info" / "warning" / "error" / …) passed to each
channel (which may use it for urgency/formatting); a channel is free to ignore it.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from lhassa.notify.channels import active_channels


async def notify(
    text: str, *, level: str = "info", config=None, kind: Optional[str] = None, **kwargs
) -> bool:
    """Fan ``text`` out to every configured notification channel.

    ``kind`` names the notification's shape for the voice layer (see
    ``lhassa.notify.voice.KINDS`` — "alert" / "reminder" / "action-result" /
    "approval" / "report" / "generic"); callers that omit it get a cheap
    deterministic sniff instead. ``**kwargs`` carries channel-specific extras
    (e.g. Telegram's ``parse_mode`` / ``reply_markup``, an inline keyboard)
    straight through to each channel's ``send``; a channel with no use for them
    (log, desktop) simply ignores them.

    Returns ``True`` if at least one channel delivered the message, ``False`` if
    none did (nothing configured, or every channel declined/failed). Fully
    defensive: a channel raising is logged and treated as a non-delivery; this
    never propagates.
    """
    if config is None:
        from lhassa.config import Config

        config = Config()

    try:
        from lhassa.notify.voice import revoice

        text = await revoice(text, kind=kind, config=config)
    except Exception as exc:  # voicing must never cost the owner a notification
        logger.debug(f"notify: voice layer degraded to verbatim text: {exc}")

    delivered = False
    for channel in active_channels(config):
        try:
            if await channel.send(text, level=level, config=config, **kwargs):
                delivered = True
        except Exception as exc:  # a channel must never break the dispatcher
            name = getattr(channel, "name", "?")
            logger.warning(f"notify: channel {name!r} raised: {exc}")

    return delivered
