"""Desktop notification channel — a libnotify (``notify-send``) toast on Linux.

A registered :class:`~lhassa.notify.channels.Channel` that pops a desktop toast via
``notify-send``. It mirrors the cross-platform notifier pattern already in the repo
(``lhassa/voice/notifier.py``) but is intentionally a small, self-contained
``notify-send`` call so this module depends on nothing from the voice stack.

Discipline matches the rest of :mod:`lhassa.notify`:
- **configured** == the ``notify-send`` binary exists on ``PATH``;
- **never raises** — a missing binary or a failed call is logged and yields
  ``False``, so a notification can never break the caller;
- the subprocess call has a short timeout and never propagates errors.
"""

from __future__ import annotations

import os
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


def _platform_argv(title: str, message: str):
    """Non-Linux toast argv from the host layer, else ``None``.

    Consulted only off Linux so the tier-1 ``notify-send`` path is untouched.
    """
    from lhassa.host import current_platform_name, get_platform

    if current_platform_name() == "linux":
        return None
    plat = get_platform()
    if plat is None:
        return None
    return plat.notify_argv(title, message)


@register_channel("desktop")
class DesktopChannel:
    """Pop a desktop toast through ``notify-send`` (Linux/libnotify)."""

    name = "desktop"

    def is_configured(self, config=None) -> bool:
        """True when this host has any desktop-toast mechanism.

        ``notify-send`` on Linux; off Linux the host platform layer answers
        (``osascript`` on macOS). Unconfigured stays SILENT by design — the
        dispatcher simply skips the channel and Telegram still delivers.
        """
        if _platform_argv("probe", "probe") is not None:
            return True
        return shutil.which(_NOTIFY_SEND) is not None

    async def send(self, text: str, *, level: str = "info", config=None, **kwargs) -> bool:
        """Show ``text`` as a desktop toast. Never raises; returns delivered?.

        ``**kwargs`` (e.g. Telegram's ``parse_mode`` / ``reply_markup``) has no
        equivalent on a desktop toast and is simply ignored.
        """
        if not self.is_configured(config):
            return False
        try:
            self._notify(text, level)
            return True
        except Exception as exc:  # a Channel never raises
            logger.warning(f"notify: desktop channel failed: {exc}")
            return False

    @staticmethod
    def _notify(text: str, level: str) -> None:
        argv = _platform_argv(_APP_NAME, text)
        if argv is not None:
            # The platform layer passes user text through the environment
            # rather than interpolating it into a script — same discipline as
            # ``voice/notifier.py``'s AppleScript call.
            env = os.environ.copy()
            env["_LHASSA_TITLE"] = _APP_NAME
            env["_LHASSA_MSG"] = text
            subprocess.run(argv, timeout=_TIMEOUT, capture_output=True, env=env)
            return
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
