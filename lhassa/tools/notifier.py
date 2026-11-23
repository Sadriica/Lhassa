# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Notification router: speaks through a registered speaker when one is live,
falls back to a desktop notification otherwise.

Platform support:
  Linux  ; notify-send (libnotify)
  macOS  ; osascript (built-in)
  Windows. PowerShell New-BurntToastNotification or a msg-box fallback

The spoken path is a seam, not an import: a surface that can talk (the voice
pack) calls :func:`register_speaker` with an async ``speak(message) -> bool``.
``notify()`` tries each speaker in turn and stops at the first that returns
True; with no speaker registered (core alone, or a headless role) every
notification goes to the desktop. A speaker that raises is logged and skipped.
"""

import platform
import subprocess
from typing import Awaitable, Callable

from loguru import logger

_SYSTEM = platform.system()

Speaker = Callable[[str], Awaitable[bool]]
_SPEAKERS: list[Speaker] = []


def register_speaker(speaker: Speaker) -> Speaker:
    """Add a speaker (idempotent: the same callable is registered once)."""
    if speaker not in _SPEAKERS:
        _SPEAKERS.append(speaker)
    return speaker


def unregister_speaker(speaker: Speaker) -> None:
    """Remove a speaker if present; never raises."""
    if speaker in _SPEAKERS:
        _SPEAKERS.remove(speaker)


async def notify(message: str, title: str = "Lhassa"):
    """Send a notification through the best available channel."""
    for speaker in list(_SPEAKERS):
        try:
            if await speaker(message):
                return
        except Exception as e:
            logger.warning(f"Spoken notify failed, falling back to desktop: {e}")

    _desktop_notify(title, message)


def _desktop_notify(title: str, message: str):
    try:
        if _SYSTEM == "Darwin":
            _notify_macos(title, message)
        elif _SYSTEM == "Windows":
            _notify_windows(title, message)
        else:
            _notify_linux(title, message)
    except Exception as e:
        logger.warning(f"Desktop notification error: {e}")


def _notify_linux(title: str, message: str):
    try:
        subprocess.run(
            ["notify-send", "--app-name=Lhassa", title, message],
            timeout=5,
            capture_output=True,
        )
    except FileNotFoundError:
        print(f"\033[1;33m[{title}]\033[0m {message}")


def _notify_macos(title: str, message: str):
    # Use environment variables + AppleScript's `system attribute` so no
    # user-controlled text is ever interpolated into the script string.
    import os
    env = os.environ.copy()
    env["_LHASSA_TITLE"] = title
    env["_LHASSA_MSG"] = message
    script = (
        'set t to system attribute "_LHASSA_TITLE"\n'
        'set m to system attribute "_LHASSA_MSG"\n'
        'display notification m with title t'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            timeout=5,
            capture_output=True,
            env=env,
        )
    except FileNotFoundError:
        print(f"\033[1;33m[{title}]\033[0m {message}")


def _notify_windows(title: str, message: str):
    # Try PowerShell BurntToast (optional module), fall back to a VBScript msg box
    safe_title = title.replace("'", "''")
    safe_msg = message.replace("'", "''")
    ps_script = (
        f"if (Get-Module -ListAvailable -Name BurntToast) {{"
        f" New-BurntToastNotification -Text '{safe_title}','{safe_msg}' }}"
        f" else {{"
        f" [System.Windows.MessageBox]::Show('{safe_msg}','{safe_title}') | Out-Null }}"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            timeout=8,
            capture_output=True,
        )
    except FileNotFoundError:
        print(f"\033[1;33m[{title}]\033[0m {message}")
