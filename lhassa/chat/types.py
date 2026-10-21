# SPDX-License-Identifier: AGPL-3.0-or-later
"""The chat engine's transport-neutral contract.

A transport (the telegram pack's poller, a future web chat, a test harness)
turns whatever arrived on its wire into an :class:`InboundMessage`, hands it to
:func:`lhassa.chat.engine.handle` (or :func:`~lhassa.chat.engine.respond`), and
renders the :class:`Reply` it gets back. Nothing here knows Telegram: buttons
are ``(label, data)`` pairs the transport draws however its wire draws them,
and a tap comes back as ``InboundMessage.callback`` carrying that ``data``.

A reply never carries a file. A file reaching the owner is a work product, and
work products travel UP the hierarchy through :func:`lhassa.org.deliver.deliver`
(the standing principle); the chat door only ever speaks text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

__all__ = [
    "Button",
    "Reply",
    "InboundDocument",
    "InboundMessage",
    "Sender",
    "Fetcher",
    "CHAT_SOURCE",
]

#: The wake label every chat-door episode carries (``despertar_por``), and the
#: actor id chat-minted tasks are signed with. It predates the engine split and
#: readers key on it (``agents/utility.py``'s owner wake set, the repetition
#: detector's ``telegram::responder`` pattern), so it stays the literal the
#: Telegram door always logged.
CHAT_SOURCE = "telegram"


@dataclass(frozen=True)
class Button:
    """One tappable choice under a reply.

    ``data`` is opaque to the transport: it is echoed back verbatim as
    :attr:`InboundMessage.callback` when the owner taps the button.
    """

    label: str
    data: str


@dataclass(frozen=True)
class Reply:
    """What the engine wants said back.

    * ``text``: the chat message; empty means "nothing to post in the chat".
    * ``buttons``: one row of :class:`Button` under the message.
    * ``ack``: only for a tap (``InboundMessage.callback``): the short
      acknowledgment the transport shows on the tapped button. ``None`` for an
      ordinary message.
    * ``followups``: zero-arg coroutine factories the transport starts, fire
      and forget, AFTER the reply was sent (an approved coding run, an action
      that runs now and reports back). :func:`lhassa.chat.engine.respond`
      does this for a transport that uses it.
    """

    text: str
    buttons: tuple[Button, ...] = ()
    ack: Optional[str] = None
    followups: tuple[Callable[[], Awaitable[Any]], ...] = field(default=(), compare=False)


#: Sends one :class:`Reply` to the chat the message came from. Never raises;
#: returns whether it was delivered.
Sender = Callable[[Reply], Awaitable[bool]]

#: Downloads an inbound document's bytes, or ``None`` when it cannot.
Fetcher = Callable[[], Awaitable[Optional[bytes]]]


@dataclass(frozen=True)
class InboundDocument:
    """An uploaded file, described without the transport's wire format.

    ``raw`` is the transport's own description (kept for hooks that want a
    detail the neutral fields do not carry); ``fetch`` downloads the bytes.
    """

    file_id: str
    file_name: str = ""
    caption: Optional[str] = None
    fetch: Optional[Fetcher] = None
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """The ``{file_id, file_name, ...}`` mapping inbound document hooks get."""
        out = dict(self.raw)
        out.setdefault("file_id", self.file_id)
        if self.file_name:
            out.setdefault("file_name", self.file_name)
        return out


@dataclass(frozen=True)
class InboundMessage:
    """One accepted inbound event from the owner.

    Exactly one of ``text`` / ``document`` / ``callback`` is the event; the
    transport has already decided the sender is the owner (an owner-only door
    is a transport concern, it knows the wire's identities).

    * ``send``: how the engine (and the inbound hooks it runs) speaks out of
      band, after or instead of the returned reply: follow-ups, a hook that
      replies itself. When ``None`` the engine falls back to the configured
      notify channels.
    * ``acknowledge``: for a tap, how the transport acknowledges the button
      (used by :func:`lhassa.chat.engine.respond` with ``Reply.ack``).
    """

    chat_id: str = ""
    text: str = ""
    document: Optional[InboundDocument] = None
    callback: Optional[str] = None
    send: Optional[Sender] = None
    acknowledge: Optional[Callable[[str], Awaitable[Any]]] = None
