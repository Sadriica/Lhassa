"""Desktop notification channel — a libnotify (``notify-send``) toast on Linux.

A registered :class:`~lhassa.notify.channels.Channel` that pops a desktop toast via
``notify-send``. It mirrors the cross-platform notifier pattern already in the repo
(``lhassa/voice/notifier.py``) but is intentionally a small, self-contained
``notify-send`` call so this module depends on nothing from the voice stack.

Discipline matches the rest of :mod:`lhassa.notify`:
- **configured** == the ``notify-send`` binary exists on ``PATH``;
- **never raises** — a missing binary or a failed call is logged and yields
  ``False``, so a notification can never break the caller;
- the blocking ``subprocess`` call runs in a thread so it never stalls the loop.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

from loguru import logger

from lhassa.notify.channels import register_channel

# The Linux libnotify CLI. Present == this channel is configured.
_NOTIFY_SEND = "notify-send"
_APP_NAME = "Lhassa"
_TIMEOUT = 5.0

# Advisory severity → notify-send urgency.
_URGENCY = {
    "debug": "low",
    "info": "low",
    "success": "low",
    "warning": "normal",
    "error": "critical",
    "critical": "critical",
}


@register_channel("desktop")
class DesktopChannel:
    """Pop a desktop toast through ``notify-send`` (Linux/libnotify)."""

    name = "desktop"

    def is_configured(self, config=None) -> bool:
        """True when the ``notify-send`` binary is available on ``PATH``."""
        return shutil.which(_NOTIFY_SEND) is not None

    async def send(self, text: str, *, level: str = "info", config=None) -> bool:
        """Show ``text`` as a desktop toast. Never raises; returns delivered?."""
        if shutil.which(_NOTIFY_SEND) is None:
            return False
        try:
            await asyncio.to_thread(self._notify, text, level)
            return True
        except Exception as exc:  # a Channel never raises
            logger.warning(f"notify: desktop channel failed: {exc}")
            return False

    @staticmethod
    def _notify(text: str, level: str) -> None:
        urgency = _URGENCY.get(str(level).lower(), "low")
        subprocess.run(
            [
                _NOTIFY_SEND,
                f"--app-name={_APP_NAME}",
                f"--urgency={urgency}",
                _APP_NAME,
                text,
            ],
            timeout=_TIMEOUT,
            capture_output=True,
        )
