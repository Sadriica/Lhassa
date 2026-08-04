# SPDX-License-Identifier: AGPL-3.0-or-later
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


#: Separators that mark where a message's headline ends and its detail begins,
#: in the order the messages actually use them.
_HEADLINE_SEPARATORS = (" — ", " · ", ": ")

#: A headline longer than this is detail wearing a title's clothes.
_HEADLINE_MAX = 60


def split_headline(text: str) -> "tuple[str, str]":
    """Split a notification into a toast ``(summary, body)``.

    A toast already shows "Lhassa" as the application name, so repeating it as
    the summary wasted the one line the owner actually reads at a glance and
    pushed the real content down. This lifts the message's own opening clause
    into the summary and leaves the rest as the body.

    Degrades to the previous behaviour: a message with no separator, or whose
    first clause is too long to be a headline, keeps the application name as
    the summary and the whole text as the body. An empty body would leave the
    toast's prominent line blank, so a headline is lifted only when the message
    actually has one.
    """
    text = (text or "").strip()
    if not text:
        return (_APP_NAME, "")
    for sep in _HEADLINE_SEPARATORS:
        head, found, tail = text.partition(sep)
        if found and tail.strip() and len(head) <= _HEADLINE_MAX:
            return (head.strip(), tail.strip())
    return (_APP_NAME, text)


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
        title, body = split_headline(text)
        argv = _platform_argv(title, body)
        if argv is not None:
            # The platform layer passes user text through the environment
            # rather than interpolating it into a script — same discipline as
            # ``voice/notifier.py``'s AppleScript call.
            env = os.environ.copy()
            env["_LHASSA_TITLE"] = title
            env["_LHASSA_MSG"] = body
            subprocess.run(argv, timeout=_TIMEOUT, capture_output=True, env=env)
            return
        urgency = _URGENCY.get(str(level).lower(), "low")
        subprocess.run(
            [
                _NOTIFY_SEND,
                f"--app-name={_APP_NAME}",
                f"--urgency={urgency}",
                title,
                body,
            ],
            timeout=_TIMEOUT,
            capture_output=True,
        )
