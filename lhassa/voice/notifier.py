"""
Notification router — speaks via TTS when the voice pipeline is active,
falls back to a desktop notification when idle.

Platform support:
  Linux   — notify-send (libnotify)
  macOS   — osascript (built-in)
  Windows — PowerShell New-BurntToastNotification or a msg-box fallback
"""

import platform
import subprocess
from loguru import logger

from pipecat.frames.frames import TTSSpeakFrame
from lhassa.state import state

_SYSTEM = platform.system()


async def notify(message: str, title: str = "Lhassa"):
    """Send a notification through the best available channel."""
    if state.is_voice_active and state.pipeline_task is not None:
        try:
            await state.pipeline_task.queue_frame(TTSSpeakFrame(text=message))
            return
        except Exception as e:
            logger.warning(f"TTS notify failed, falling back to desktop: {e}")

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
