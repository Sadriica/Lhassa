# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inbound chat hooks; the seam a pack uses to take over a Telegram message.

The string-returning :data:`lhassa.notify.commands.COMMANDS` registry covers most
chat commands. A few capabilities need more than a reply string: they reply with a
file, keep per-chat state between messages, or consume an uploaded document. The
cv pack is the first (``/cv``, the held-CV follow-up, the ``/cvnewbase`` upload).
Those plug in here instead of being hard-wired into
:mod:`lhassa.notify.telegram_inbound`.

An :class:`InboundHook` carries up to three callables:

* ``on_text(text, *, config, chat_id) -> bool``: runs first for every accepted
  text message; returning ``True`` means "handled, stop here".
* ``on_document(document, caption, *, config, chat_id) -> bool``: runs for an
  uploaded document; the first hook returning ``True`` takes it.
* ``reset()``: forget any per-chat state (the test suite calls it between tests,
  through :func:`lhassa.notify.telegram_inbound.reset_conversation_state`).

Hooks run in registration order. A hook that raises is logged and treated as "not
handled", so a broken pack never takes the poller down, and with no hook
registered every runner is a no-op that returns ``False``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from lhassa.registry import Registry

TextHook = Callable[..., Awaitable[bool]]
DocumentHook = Callable[..., Awaitable[bool]]


@dataclass(frozen=True)
class InboundHook:
    """One pack's inbound handlers; any of them may be ``None``."""

    name: str
    on_text: Optional[TextHook] = None
    on_document: Optional[DocumentHook] = None
    reset: Optional[Callable[[], None]] = None


INBOUND_HOOKS: Registry[InboundHook] = Registry("inbound hook")


def register_inbound_hook(
    name: str,
    *,
    on_text: Optional[TextHook] = None,
    on_document: Optional[DocumentHook] = None,
    reset: Optional[Callable[[], None]] = None,
) -> InboundHook:
    """Register a hook under ``name``. Re-registering the same name is a no-op."""
    existing = INBOUND_HOOKS.get_or_none(name)
    if existing is not None:
        return existing
    hook = InboundHook(name=name, on_text=on_text, on_document=on_document, reset=reset)
    INBOUND_HOOKS.register(name, hook)
    return hook


async def run_text_hooks(text: str, *, config: Any, chat_id: str = "") -> bool:
    """Offer ``text`` to every text hook; ``True`` once one handles it. Never raises."""
    for hook in INBOUND_HOOKS.all():
        if hook.on_text is None:
            continue
        try:
            if await hook.on_text(text, config=config, chat_id=chat_id):
                return True
        except Exception as exc:  # a broken hook must never kill the poller
            logger.warning(f"inbound hook {hook.name!r}: text handling failed: {exc}")
    return False


async def run_document_hooks(
    document: dict, caption: Any, *, config: Any, chat_id: str = ""
) -> bool:
    """Offer an uploaded ``document`` to every document hook. Never raises."""
    for hook in INBOUND_HOOKS.all():
        if hook.on_document is None:
            continue
        try:
            if await hook.on_document(document, caption, config=config, chat_id=chat_id):
                return True
        except Exception as exc:  # a broken hook must never kill the poller
            logger.warning(f"inbound hook {hook.name!r}: document handling failed: {exc}")
    return False


def reset_all() -> None:
    """Call every hook's ``reset``. Never raises."""
    for hook in INBOUND_HOOKS.all():
        if hook.reset is None:
            continue
        try:
            hook.reset()
        except Exception as exc:
            logger.warning(f"inbound hook {hook.name!r}: reset failed: {exc}")


__all__ = [
    "INBOUND_HOOKS",
    "InboundHook",
    "register_inbound_hook",
    "reset_all",
    "run_document_hooks",
    "run_text_hooks",
]
