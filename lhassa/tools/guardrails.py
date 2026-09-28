# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared safety checks for shell and filesystem tools."""

from __future__ import annotations

import re
from pathlib import Path

from lhassa.config import Config

_DESTRUCTIVE_SHELL_PATTERNS = [
    re.compile(r"^\s*rm\s+-rf?\s+/(?:\s|$)"),
    re.compile(r"^\s*sudo\s+rm\s+-rf?\s+/(?:\s|$)"),
    re.compile(r"^\s*mkfs(?:\.\w+)?\b"),
    re.compile(r"^\s*dd\b"),
    re.compile(r"^\s*(?:shutdown|reboot|poweroff|halt)\b"),
    re.compile(r"^\s*init\s+[06](?:\s|$)"),
]

# Every pattern above anchors at the start of the string, so a command has to be
# split into its segments before matching or the anchor is the bypass: ``rm -rf
# /`` was blocked while ``true && rm -rf /`` ran. Shell operators that begin a
# NEW command, plus the openers of a command substitution.
_SEGMENT_SPLIT = re.compile(r"(?:\|\||&&|[;\n|&]|\$\(|`)")

# Things that outlive the turn or escalate privilege. Found the hard way: asked
# to run a dashboard daily, Lhassa installed a systemd user unit and enabled its
# timer. Nothing about that was destructive, so nothing stopped it; the owner
# had asked for "daily", not for a unit on their machine. Persistence is where a
# tool call stops being a turn and becomes a change to the box.
_PERSISTENT_SHELL_PATTERNS = [
    re.compile(r"^\s*(?:sudo\s+)?systemctl\b.*\b(?:enable|disable|mask|unmask)\b"),
    re.compile(r"^\s*(?:sudo\s+)?crontab\b"),
    re.compile(r"^\s*(?:sudo\s+)?(?:pacman|apt|apt-get|yay|dnf|zypper|brew)\b"),
    re.compile(r"^\s*sudo\b"),
    # A write INTO a persistence location (a redirect, tee, cp/mv/install onto
    # it). Reading from these paths stays free.
    # ``[^;&|]*?`` rather than ``\S*``: the destination is often not the first
    # argument (``cp unit.service /etc/systemd/system/``), but it never crosses
    # a segment boundary, which is what keeps this from matching the next
    # command along.
    re.compile(
        r"(?:>>?|\btee\b|\bcp\b|\bmv\b|\binstall\b)[^;&|]*?"
        r"(?:/etc/|/usr/lib/systemd/|\.config/systemd/|/etc/systemd/"
        r"|\.bashrc|\.zshrc|\.profile|/etc/cron)"
    ),
]


def _segments(command: str) -> list[str]:
    """The command split into the pieces a shell would each run on its own."""
    return [seg for seg in (s.strip() for s in _SEGMENT_SPLIT.split(command)) if seg]


def validate_shell_command(command: str) -> str | None:
    """``None`` if ``command`` may run, else the reason it was blocked.

    A denylist over each segment of the command. It exists to stop an ACCIDENT:
    a model reaching for ``rm -rf /`` or rebooting the owner's laptop mid-turn.
    It is explicitly NOT an adversarial boundary; a determined caller can defeat
    any regex denylist (quoting tricks, base64, a shell variable holding the
    verb). The real boundary for untrusted code is the quarantine sandbox in
    ``security/quarantine.py``; this only raises the floor for trusted-but-
    fallible callers.
    """
    command = command.strip()
    if not command:
        return "Command is required."

    config = Config()
    segments = _segments(command)

    if not config.allow_destructive_shell:
        for segment in segments:
            for pattern in _DESTRUCTIVE_SHELL_PATTERNS:
                if pattern.search(segment):
                    return (
                        "Blocked potentially destructive shell command. "
                        "Set LHASSA_ALLOW_DESTRUCTIVE_SHELL=1 to allow it explicitly."
                    )

    if not config.allow_persistent_shell:
        for segment in segments:
            for pattern in _PERSISTENT_SHELL_PATTERNS:
                if pattern.search(segment):
                    return (
                        "Blocked: this command persists past the turn or escalates "
                        "privilege (systemd, cron, package install, sudo, or a write "
                        "into /etc or a shell rc). Ask the owner to run it, or set "
                        "LHASSA_ALLOW_PERSISTENT_SHELL=1 to allow it explicitly."
                    )
    return None


def validate_write_path(path_str: str) -> tuple[Path | None, str | None]:
    path_str = path_str.strip()
    if not path_str:
        return None, "Path is required for write_file."

    path = Path(path_str).expanduser()
    root = Config().file_write_root.strip()
    if not root:
        return path, None

    root_path = Path(root).expanduser().resolve()
    target_path = path.resolve(strict=False)
    try:
        target_path.relative_to(root_path)
    except ValueError:
        return None, f"Write blocked: {target_path} is outside LHASSA_FILE_WRITE_ROOT={root_path}"

    return path, None
