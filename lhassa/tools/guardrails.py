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


def validate_shell_command(command: str) -> str | None:
    command = command.strip()
    if not command:
        return "Command is required."

    if Config().allow_destructive_shell:
        return None

    for pattern in _DESTRUCTIVE_SHELL_PATTERNS:
        if pattern.search(command):
            return (
                "Blocked potentially destructive shell command. "
                "Set LHASSA_ALLOW_DESTRUCTIVE_SHELL=1 to allow it explicitly."
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
