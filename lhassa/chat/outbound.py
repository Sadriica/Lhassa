# SPDX-License-Identifier: AGPL-3.0-or-later
"""How the chat engine speaks out of band, without knowing the transport.

Every out-of-band message (a follow-up after a tap, an action's result when the
deliver seam is down, an inbound hook's own reply) goes through a
:data:`~lhassa.chat.types.Sender`. The transport supplies one on the
:class:`~lhassa.chat.types.InboundMessage`; when it does not, the engine falls
back to :func:`default_sender`, the configured notify channels.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from loguru import logger

from lhassa.chat.types import Reply, Sender

__all__ = ["default_sender", "resolve_sender", "say", "text_sender"]


def default_sender(config) -> Sender:
    """A :data:`Sender` that posts the reply's text on every active channel.

    Plain text only (buttons need a transport that can draw them) and no voice
    pass: this is a reply, already worded, not a notification. Never raises.
    """

    async def _send(reply: Reply) -> bool:
        from lhassa.notify.channels import active_channels

        delivered = False
        try:
            channels = active_channels(config)
        except Exception as exc:
            logger.warning(f"chat: no channels to reply on ({exc})")
            return False
        for channel in channels:
            try:
                if await channel.send(reply.text, config=config):
                    delivered = True
            except Exception as exc:  # a channel must never break the reply
                logger.warning(f"chat: channel {getattr(channel, 'name', '?')!r} raised: {exc}")
        return delivered

    return _send


def resolve_sender(send: Optional[Sender], config) -> Sender:
    """``send`` itself, or the channel fallback when the transport gave none."""
    return send if send is not None else default_sender(config)


async def say(send: Sender, text: str, *, buttons=()) -> bool:
    """Send ``text`` through ``send``. Never raises."""
    try:
        return bool(await send(Reply(text=text, buttons=tuple(buttons))))
    except Exception as exc:
        logger.warning(f"chat: reply send failed: {exc}")
        return False


def text_sender(send: Sender) -> Callable[[str], Awaitable[bool]]:
    """Adapt a :data:`Sender` to the ``reply(text)`` callable inbound hooks get."""

    async def _reply(text: str) -> bool:
        return await say(send, text)

    return _reply
