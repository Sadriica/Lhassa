"""Outbound notifications for Lhassa.

A thin, credential-gated adapter layer. The only channel today is Telegram
(:mod:`lhassa.notify.telegram`); more channels can be added behind the same
``send`` / ``is_configured`` shape. Nothing here is wired into the heartbeat loop
yet — these are the adapters only.
"""

from lhassa.notify import telegram
from lhassa.notify.telegram import is_configured, send

__all__ = ["telegram", "send", "is_configured"]
