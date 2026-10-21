# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inbound chat hooks; the seam a pack uses to take over a chat message.

The string-returning :data:`lhassa.notify.commands.COMMANDS` registry covers most
chat commands. A few capabilities need more than a reply string: they reply with a
file, keep per-chat state between messages, or consume an uploaded document. The
cv pack is the first (``/cv``, the held-CV follow-up, the ``/cvnewbase`` upload).
Those plug in here instead of being hard-wired into the chat engine
(:mod:`lhassa.chat.engine`), and they never see the transport:

* ``on_text(text, *, config, chat_id, reply) -> bool``: runs first for every
  accepted text message; returning ``True`` means "handled, stop here".
  ``reply(text) -> bool`` posts a message back in the same chat.
* ``on_document(document, caption, *, config, chat_id, reply, fetch) -> bool``:
  runs for an uploaded document (``document`` is a ``{file_id, file_name}``
  mapping); the first hook returning ``True`` takes it. ``fetch() -> bytes |
  None`` downloads it.
* ``reset()``: forget any per-chat state (the test suite calls it between tests,
  through :func:`lhassa.chat.reset_conversation_state`).

Hooks run in registration order. A hook that raises is logged and treated as "not
handled", so a broken pack never takes the door down, and with no hook
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


def _reply_or_default(reply: Optional[Callable[..., Awaitable[bool]]], config: Any):
    if reply is not None:
        return reply
    from lhassa.chat.outbound import default_sender, text_sender

    return text_sender(default_sender(config))


async def run_text_hooks(
    text: str, *, config: Any, chat_id: str = "", reply=None
) -> bool:
    """Offer ``text`` to every text hook; ``True`` once one handles it. Never raises."""
    hooks = [h for h in INBOUND_HOOKS.all() if h.on_text is not None]
    if not hooks:
        return False
    reply = _reply_or_default(reply, config)
    for hook in hooks:
        try:
            if await hook.on_text(text, config=config, chat_id=chat_id, reply=reply):
                return True
        except Exception as exc:  # a broken hook must never kill the door
            logger.warning(f"inbound hook {hook.name!r}: text handling failed: {exc}")
    return False


async def _no_fetch() -> Optional[bytes]:
    return None


async def run_document_hooks(
    document: dict,
    caption: Any,
    *,
    config: Any,
    chat_id: str = "",
    reply=None,
    fetch=None,
) -> bool:
    """Offer an uploaded ``document`` to every document hook. Never raises."""
    hooks = [h for h in INBOUND_HOOKS.all() if h.on_document is not None]
    if not hooks:
        return False
    reply = _reply_or_default(reply, config)
    fetch = fetch if fetch is not None else _no_fetch
    for hook in hooks:
        try:
            if await hook.on_document(
                document, caption, config=config, chat_id=chat_id, reply=reply, fetch=fetch
            ):
                return True
        except Exception as exc:  # a broken hook must never kill the door
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
