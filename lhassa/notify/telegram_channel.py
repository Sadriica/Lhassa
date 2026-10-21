# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Telegram adapter as a notify :class:`~lhassa.notify.channels.Channel`.

Wraps :func:`lhassa.notify.telegram.send` / ``is_configured`` so Telegram joins
the channel registry, and offers the optional file capability
(``send_document``) the hierarchy's up-direction seam
(:func:`lhassa.org.deliver.deliver`) delivers files through. Every method is
defensively wrapped even though the adapters underneath already never raise.
"""

from __future__ import annotations

from loguru import logger

from lhassa.notify import telegram


class TelegramChannel:
    """The credential-gated Telegram adapter, as a :class:`Channel`."""

    name = "telegram"

    def is_configured(self, config) -> bool:
        return telegram.is_configured(config)

    async def send(self, text: str, *, level: str = "info", config=None, **kwargs) -> bool:
        try:
            return await telegram.send(text, config=config, **kwargs)
        except Exception as exc:  # the adapter shouldn't, but a Channel never raises
            logger.warning(f"notify: telegram channel raised: {exc}")
            return False

    async def send_document(self, path, *, caption: str = "", config=None) -> bool:
        try:
            from lhassa.notify import telegram_api

            return await telegram_api.send_document(path, caption=caption, config=config)
        except Exception as exc:  # a Channel never raises
            logger.warning(f"notify: telegram channel raised sending a file: {exc}")
            return False


def register() -> None:
    """Register the channel once (idempotent)."""
    from lhassa.notify.channels import CHANNELS, register_channel

    if "telegram" not in CHANNELS:
        register_channel("telegram", TelegramChannel())
