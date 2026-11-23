# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack entry point for Telegram (``lhassa.plugins`` -> ``telegram``).

Importing this module registers the pack and the ``telegram`` notify channel
(:class:`lhassa.telegram.channel.TelegramChannel`). The poller is its own
process (``python -m lhassa.telegram.poller``, the ``lhassa-telegram`` unit),
so nothing here starts it; httpx is imported only when a message is sent.
"""

from __future__ import annotations

from lhassa.extensions import register_pack

register_pack(
    "telegram",
    "Telegram: the Bot API client, the chat poller and the telegram notify channel",
    module=__name__,
)

from lhassa.telegram.channel import register as _register_channel  # noqa: E402

_register_channel()
