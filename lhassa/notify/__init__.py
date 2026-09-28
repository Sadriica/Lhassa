# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound notifications for Lhassa.

A thin, credential-gated adapter layer plus a fan-out :func:`notify` dispatcher
driven by a **modular channel registry** (:mod:`lhassa.notify.channels`). Built-in
channels are Telegram, a desktop toast, and a log sink; new ones are drop-in (a
registered module; see :func:`~lhassa.notify.channels.register_channel`), never an
edit to core. :func:`notify` is the single entry point other code calls; importing
this package registers all built-in channels.
"""

from lhassa.notify import telegram
from lhassa.notify.channels import (
    CHANNELS,
    Channel,
    active_channels,
    register_channel,
)
from lhassa.notify.dispatch import notify
from lhassa.notify.telegram import is_configured, send

__all__ = [
    "telegram",
    "send",
    "is_configured",
    "notify",
    "Channel",
    "CHANNELS",
    "register_channel",
    "active_channels",
]
