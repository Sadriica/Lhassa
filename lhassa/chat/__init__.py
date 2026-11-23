# SPDX-License-Identifier: AGPL-3.0-or-later
"""The chat engine: the brain's conversational door, transport-agnostic.

A transport turns its wire into an :class:`InboundMessage` and renders the
:class:`Reply` that :func:`handle` returns (or lets :func:`respond` play it
through the message's own ``send`` / ``acknowledge``). The telegram pack's
poller is one transport; the engine knows none of them.

Modules: :mod:`~lhassa.chat.types` (the contract), :mod:`~lhassa.chat.engine`
(the turn), :mod:`~lhassa.chat.think` (the tool-less brain think),
:mod:`~lhassa.chat.markers` (``[[research:]]`` / ``[[action:]]`` /
``[[remember:]]``), :mod:`~lhassa.chat.callbacks` (Approve/Deny taps),
:mod:`~lhassa.chat.rules` (watch rules), :mod:`~lhassa.chat.state` (history
and sticky topic), :mod:`~lhassa.chat.outbound` (out-of-band sends).
"""

from lhassa.chat.engine import handle, respond
from lhassa.chat.state import reset_conversation_state
from lhassa.chat.types import Button, InboundDocument, InboundMessage, Reply, Sender

__all__ = [
    "Button",
    "InboundDocument",
    "InboundMessage",
    "Reply",
    "Sender",
    "handle",
    "respond",
    "reset_conversation_state",
]
