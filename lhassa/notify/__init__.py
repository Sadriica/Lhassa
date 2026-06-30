"""Outbound notifications for Lhassa.

A thin, credential-gated adapter layer plus a fan-out :func:`notify` dispatcher.
The only outbound channel today is Telegram (:mod:`lhassa.notify.telegram`); more
channels can be added behind the same ``send`` / ``is_configured`` shape and wired
into the dispatcher. :func:`notify` is the single entry point other code calls;
the per-channel ``send`` is the adapter. Nothing here is wired into the heartbeat
loop yet.
"""

from lhassa.notify import telegram
from lhassa.notify.dispatch import notify
from lhassa.notify.telegram import is_configured, send

__all__ = ["telegram", "send", "is_configured", "notify"]
