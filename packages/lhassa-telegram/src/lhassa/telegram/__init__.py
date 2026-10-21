# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Telegram transport (the lhassa-telegram pack).

* :mod:`lhassa.telegram.client`: ``sendMessage`` (``send``, ``is_configured``);
* :mod:`lhassa.telegram.api`: the other Bot API calls (``getFile``,
  ``sendDocument``, ``setMyCommands``, ``answerCallbackQuery``);
* :mod:`lhassa.telegram.channel`: the ``telegram`` notify channel, with the
  optional file capability :func:`lhassa.org.deliver.deliver` sends through;
* :mod:`lhassa.telegram.poller`: the getUpdates long-poll that turns the
  owner's chat into :class:`lhassa.chat.InboundMessage` turns
  (``python -m lhassa.telegram.poller``).

What to answer is the chat engine's job (:mod:`lhassa.chat`, core); this pack
only knows the wire. Its config knobs (``LHASSA_TELEGRAM_*``) stay in core's
``config.py``.
"""
