# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound notifications for Lhassa.

A thin, credential-gated adapter layer plus a fan-out :func:`notify` dispatcher
driven by a **modular channel registry** (:mod:`lhassa.notify.channels`). Built-in
channels are a desktop toast and a log sink; the telegram pack adds Telegram, and
any new one is drop-in (a registered module; see
:func:`~lhassa.notify.channels.register_channel`), never an edit to core. :func:`notify` is the single entry point other code calls; importing
this package registers all built-in channels.
"""

from lhassa.notify.channels import (
    CHANNELS,
    Channel,
    active_channels,
    register_channel,
)
from lhassa.notify.dispatch import notify

__all__ = [
    "notify",
    "Channel",
    "CHANNELS",
    "register_channel",
    "active_channels",
]
