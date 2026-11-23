# SPDX-License-Identifier: AGPL-3.0-or-later
"""Notification channels; a modular registry of outbound delivery targets.

The owner's standing principle is that Lhassa must be **fully elastic and highly
modular**: every swap point sits behind an interface + a registry, and new
capabilities are **drop-in** (add a module, never edit core). Outbound
notifications are one such swap point: today Telegram + a desktop toast + a log
sink, tomorrow email / SMS / a webhook; each is just another registered channel.

This module defines:

* :class:`Channel`: the Protocol every channel implements (a ``name``, a
  ``is_configured(config)`` predicate, and an ``async send(...)`` that **never
  raises** and returns whether the message was delivered);
* :data:`CHANNELS`: the module-level :class:`~lhassa.registry.Registry` of
  channels, plus :func:`register_channel`, a thin decorator/direct-call wrapper so
  a plugin can drop in a new channel **without editing core**;
* :func:`active_channels`: resolves ``config.notify_channels`` (a comma-separated
  order) into the registered **and configured** channels, in that order; unknown
  names are skipped with a warning, unconfigured channels are skipped silently;
* the optional FILE capability: a channel that can put a file in front of the
  owner also has ``async send_document(path, *, caption="", config=None) ->
  bool``. :func:`document_channel` picks the one to use and
  :func:`deliver_document` sends through it; only :mod:`lhassa.org.deliver`
  (the hierarchy's up-direction seam) calls it, and with no file-capable
  channel registered it is an honest ``(False, "none")``, never a raise.

The built-in channels are registered here (``log``) and in
:mod:`lhassa.notify.desktop` (``desktop``), which this module imports at the end
so they are present the moment ``channels`` is imported. The Telegram channel
ships in the telegram pack (``lhassa.telegram.pack`` registers it).
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from loguru import logger

from lhassa.registry import Registry

__all__ = [
    "Channel",
    "CHANNELS",
    "NO_DOCUMENT_CHANNEL",
    "register_channel",
    "active_channels",
    "document_channel",
    "deliver_document",
    "LogChannel",
]


@runtime_checkable
class Channel(Protocol):
    """One outbound notification target.

    Implementations must be **fully defensive**: :meth:`send` never raises and
    returns ``True`` only when the message was actually delivered. ``level`` is an
    advisory severity ("info" / "warning" / "error" / …) a channel may use for
    routing/formatting; it is otherwise ignored.
    """

    name: str

    def is_configured(self, config) -> bool:
        """True when this channel has everything it needs to deliver."""
        ...

    async def send(self, text: str, *, level: str = "info", config=None, **kwargs) -> bool:
        """Deliver ``text``. Never raises; returns whether it was delivered.

        ``**kwargs`` carries channel-specific extras (e.g. Telegram's ``parse_mode``
        / ``reply_markup``); a channel that has no use for them simply ignores them
        rather than raising on an unexpected keyword.
        """
        ...


# The channel registry; the single swap point for outbound delivery targets.
CHANNELS: Registry[Channel] = Registry("channel")


def register_channel(name: str, item: Optional[Channel] = None, *, override: bool = False):
    """Register a channel under ``name``: a thin wrapper over :data:`CHANNELS`.

    Usable as a **decorator** on a channel class (``@register_channel("x")``: the
    class is instantiated with no arguments and the **instance** is registered, the
    class itself is returned unchanged) or on a ready instance, and as a **direct
    call** (``register_channel("x", instance)``). A duplicate ``name`` raises unless
    ``override=True``: so two plugins can never silently clobber each other.
    """

    def _store(obj):
        instance = obj() if isinstance(obj, type) else obj
        CHANNELS.register(name, instance, override=override)
        # Return the original object so the name in the defining module still
        # points at the class/instance the author wrote.
        return obj

    if item is None:
        return _store
    return _store(item)


# Channels a capability pack provides (name-only data; core imports none of
# them). With that pack absent the name in ``notify_channels`` is expected, not
# a typo, so it is noted once at debug level instead of warned on every send.
_PACK_CHANNELS = {"telegram": "lhassa-telegram"}
_NOTED_MISSING: set[str] = set()


def _note_missing_channel(name: str) -> None:
    if name in _NOTED_MISSING:
        return
    _NOTED_MISSING.add(name)
    if name in _PACK_CHANNELS:
        logger.debug(
            f"notify: channel {name!r} comes from the {_PACK_CHANNELS[name]} pack, "
            "which is not installed (skipped)"
        )
        return
    logger.warning(
        f"notify: unknown channel {name!r} (skipped); "
        f"known: {', '.join(CHANNELS.names()) or '<none>'}"
    )


def active_channels(config) -> list[Channel]:
    """Resolve ``config.notify_channels`` into the live channels to fan out over.

    Parses the comma-separated ``notify_channels`` order and returns the
    registered **and configured** channels in that order. An unknown name is
    skipped with a warning; an unconfigured (or misbehaving) channel is skipped
    silently. When ``config`` has no ``notify_channels`` attribute the default is
    ``"telegram"``: the historical single-channel behaviour, so existing callers
    are unchanged.
    """
    raw = getattr(config, "notify_channels", "telegram")
    names = [n.strip() for n in (raw or "").split(",") if n.strip()]

    out: list[Channel] = []
    for name in names:
        channel = CHANNELS.get_or_none(name)
        if channel is None:
            _note_missing_channel(name)
            continue
        try:
            configured = channel.is_configured(config)
        except Exception as exc:  # a misbehaving predicate must not break fan-out
            logger.warning(f"notify: channel {name!r} is_configured raised: {exc}")
            continue
        if not configured:
            # Unconfigured → skipped silently (the credential-gated discipline).
            continue
        out.append(channel)
    return out


# ── the optional file capability ─────────────────────────────────────────────────

#: The channel name :func:`deliver_document` reports when no registered channel
#: can deliver a file.
NO_DOCUMENT_CHANNEL = "none"


def _can_send_documents(channel) -> bool:
    return callable(getattr(channel, "send_document", None))


def document_channel(config) -> Optional[Channel]:
    """The channel a FILE for the owner goes out on, or ``None`` if none can.

    Preference: the first file-capable channel in ``config.notify_channels``
    order that is configured; else the first registered file-capable channel
    even if unconfigured (its own credential gate then declines, honestly, the
    way it always has). Never raises.
    """
    try:
        for channel in active_channels(config):
            if _can_send_documents(channel):
                return channel
        for channel in CHANNELS.all():
            if _can_send_documents(channel):
                return channel
    except Exception as exc:
        logger.warning(f"notify: resolving a file channel failed: {exc}")
    return None


async def deliver_document(path, *, caption: str = "", config=None) -> tuple[bool, str]:
    """Send the file at ``path`` to the owner. Returns ``(delivered, channel_name)``.

    The primitive behind :func:`lhassa.org.deliver.deliver`; nothing else calls
    it (``tests/test_deliver.py`` guards that). With no file-capable channel
    registered (the telegram pack absent) it returns
    ``(False, NO_DOCUMENT_CHANNEL)`` with a warning. Never raises.
    """
    channel = document_channel(config)
    if channel is None:
        logger.warning("notify: no channel can deliver files; the file was not sent")
        return False, NO_DOCUMENT_CHANNEL
    name = getattr(channel, "name", "?")
    try:
        return bool(await channel.send_document(path, caption=caption, config=config)), name
    except Exception as exc:  # a Channel never raises, but be sure
        logger.warning(f"notify: channel {name!r} raised sending a file: {exc}")
        return False, name


# ── built-in channels ────────────────────────────────────────────────────────────


# loguru level names keyed by the advisory severity strings the dispatcher uses.
_LOG_LEVELS = {
    "debug": "DEBUG",
    "info": "INFO",
    "success": "SUCCESS",
    "warning": "WARNING",
    "error": "ERROR",
    "critical": "CRITICAL",
}


class LogChannel:
    """A trivial, always-configured channel that logs the message.

    The safe default fallback: it needs no credentials and no binary, so a box with
    nothing else set up still "delivers" a notification to the log. Always returns
    ``True``.
    """

    name = "log"

    def is_configured(self, config=None) -> bool:
        return True

    async def send(self, text: str, *, level: str = "info", config=None, **kwargs) -> bool:
        # kwargs (e.g. Telegram's parse_mode/reply_markup) are irrelevant here; a
        # log line has no notion of formatting/buttons, so they are simply ignored.
        log_level = _LOG_LEVELS.get(str(level).lower(), "INFO")
        try:
            logger.log(log_level, f"[notify] {text}")
        except Exception:
            logger.info(f"[notify] {text}")
        return True


register_channel("log", LogChannel())

# Import the desktop channel last so ``desktop`` is registered the moment this
# module is imported (it imports :func:`register_channel` from here). Done at the
# bottom to avoid an import cycle.
from lhassa.notify import desktop as _desktop  # noqa: E402,F401
