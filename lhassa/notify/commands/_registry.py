# SPDX-License-Identifier: AGPL-3.0-or-later
"""The command registry + dispatch mechanism — the swap point itself.

Split out of the former monolithic ``lhassa/notify/commands.py`` so the
registry/dispatch plumbing lives apart from the ~45 individual command
handlers (see the package ``__init__.py`` for the full map). Nothing here
changed behaviourally versus the original module.
"""

from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger

from lhassa.registry import Registry

# The command registry — the single swap point for chat slash commands. The item
# is an ``async (args, *, config=None) -> str`` handler.
COMMANDS: Registry = Registry("command")


def register_command(name: str, handler=None, *, override: bool = False):
    """Register a command handler under ``name`` — a wrapper over :data:`COMMANDS`.

    Usable as a **decorator** (``@register_command("help")`` — ``handler`` omitted,
    the decorated coroutine is registered and returned unchanged) or as a **direct
    call** (``register_command("help", handler)``). A duplicate ``name`` raises
    unless ``override=True`` — so two plugins can never silently clobber each other.
    The name is normalised to lower-case so ``/Help`` and ``/help`` are the same.
    """
    return COMMANDS.register(name.strip().lower(), handler, override=override)


def parse(text: str) -> Tuple[Optional[str], str]:
    """Split ``text`` into ``(command | None, args)``.

    A message is a command iff its first non-space character is ``/``. The leading
    ``/`` is stripped, the first whitespace-delimited token is the command name, and
    the rest (stripped) is ``args``. Telegram's ``/cmd@BotName`` form is normalised
    by dropping the ``@BotName`` suffix; the name is lower-cased. Anything that is
    not a slash command yields ``(None, text)`` so the caller can route it onward.
    """
    if not isinstance(text, str):
        return None, ""

    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, text

    body = stripped[1:].strip()
    if not body:
        # A bare "/" is not a command — let the brain handle it.
        return None, text

    parts = body.split(maxsplit=1)
    name = parts[0]
    args = parts[1].strip() if len(parts) > 1 else ""

    # Telegram addresses a bot as "/cmd@BotName" in group chats — drop the suffix.
    if "@" in name:
        name = name.split("@", 1)[0]

    name = name.lower()
    if not name:
        # e.g. "/@BotName" — no actual command name.
        return None, text
    return name, args


# Short hint returned for an unrecognised "/command".
_UNKNOWN = "Unknown command /{name} — try /help."


async def dispatch(text: str, *, config=None) -> Optional[str]:
    """Route a message to its command handler.

    Returns the handler's reply text for a known command; a short "unknown command"
    hint for an unrecognised ``/command``; and ``None`` for a plain-language message
    (the signal: let the brain answer it). Never raises — a misbehaving handler
    degrades to a friendly fallback string.
    """
    name, args = parse(text)
    if name is None:
        # Not a command → let the brain answer.
        return None

    handler = COMMANDS.get_or_none(name)
    if handler is None:
        return _UNKNOWN.format(name=name)

    try:
        return await handler(args, config=config)
    except Exception as exc:  # a command must never crash the poll loop
        logger.warning(f"commands: handler {name!r} failed: {exc}")
        return "That one stumbled on me — try it again in a moment, or /help for the map."
