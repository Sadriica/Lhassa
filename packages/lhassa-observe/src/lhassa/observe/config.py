# SPDX-License-Identifier: AGPL-3.0-or-later
"""The observer configuration: a JSON file plus flag overrides.

The default is to observe nothing. ``~/.lhassa/observe.json`` (or
``LHASSA_OBSERVE_CONFIG``) looks like::

    {
      "enabled": ["system", "sway", "shell", "files"],
      "system": {"interval": 30},
      "sway":   {"titles": false},
      "shell":  {"full_command": false},
      "files":  {"folders": [{"path": "~/notes", "recursive": true, "max_depth": 2}],
                 "max_watches": 256}
    }

A missing, unreadable or malformed file is an empty configuration (nothing runs).
Flags can enable an observer; they can never add a watched folder: folders are
declared in the file only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

OBSERVERS = ("system", "sway", "shell", "files")

DEFAULT_INTERVAL = 30.0
MIN_INTERVAL = 5.0
MAX_INTERVAL = 3600.0
DEFAULT_MAX_WATCHES = 256
HARD_MAX_WATCHES = 4096
DEFAULT_MAX_DEPTH = 2
HARD_MAX_DEPTH = 8
DEFAULT_EXCLUDE = (".git", "node_modules", "__pycache__", ".venv", ".cache")


@dataclass(frozen=True)
class Folder:
    path: str
    recursive: bool = False
    max_depth: int = DEFAULT_MAX_DEPTH


@dataclass(frozen=True)
class ObserveConfig:
    enabled: frozenset[str] = frozenset()
    interval: float = DEFAULT_INTERVAL
    sway_titles: bool = False
    shell_full_command: bool = False
    folders: tuple[Folder, ...] = ()
    max_watches: int = DEFAULT_MAX_WATCHES
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE
    problems: tuple[str, ...] = field(default=())


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        return min(hi, max(lo, float(value)))
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return min(hi, max(lo, int(value)))
    except (TypeError, ValueError):
        return default


def _section(raw: dict, key: str) -> dict:
    return raw[key] if isinstance(raw.get(key), dict) else {}


def parse_config(raw: Any) -> ObserveConfig:
    """Build a config from decoded JSON; anything malformed degrades to the default."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return ObserveConfig(problems=("config is not a JSON object",))
    enabled = raw.get("enabled", [])
    enabled_set = frozenset(
        n for n in (enabled if isinstance(enabled, list) else []) if n in OBSERVERS
    )
    sysc, swayc = _section(raw, "system"), _section(raw, "sway")
    shellc, filesc = _section(raw, "shell"), _section(raw, "files")
    folders: list[Folder] = []
    raw_folders = filesc.get("folders", [])
    for item in raw_folders if isinstance(raw_folders, list) else []:
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"].strip():
            problems.append("a folder entry without a path was ignored")
            continue
        folders.append(
            Folder(
                path=item["path"].strip(),
                recursive=bool(item.get("recursive", False)),
                max_depth=_int(item.get("max_depth"), DEFAULT_MAX_DEPTH, 0, HARD_MAX_DEPTH),
            )
        )
    exclude = filesc.get("exclude", list(DEFAULT_EXCLUDE))
    return ObserveConfig(
        enabled=enabled_set,
        interval=_num(sysc.get("interval"), DEFAULT_INTERVAL, MIN_INTERVAL, MAX_INTERVAL),
        sway_titles=bool(swayc.get("titles", False)),
        shell_full_command=bool(shellc.get("full_command", False)),
        folders=tuple(folders),
        max_watches=_int(filesc.get("max_watches"), DEFAULT_MAX_WATCHES, 1, HARD_MAX_WATCHES),
        exclude=tuple(str(e) for e in exclude) if isinstance(exclude, list) else DEFAULT_EXCLUDE,
        problems=tuple(problems),
    )


def load_config(path: str | Path) -> ObserveConfig:
    """Read the user's file. Never raises: absent or broken means observe nothing."""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError:
        return ObserveConfig()
    try:
        return parse_config(json.loads(text))
    except ValueError:
        return ObserveConfig(problems=("config is not valid JSON",))


def with_overrides(
    cfg: ObserveConfig,
    *,
    enable: frozenset[str] = frozenset(),
    interval: Optional[float] = None,
    sway_titles: Optional[bool] = None,
    shell_full_command: Optional[bool] = None,
) -> ObserveConfig:
    """Apply CLI flags on top of the file (flags may enable, never add folders)."""
    changes: dict[str, Any] = {"enabled": cfg.enabled | frozenset(enable)}
    if interval is not None:
        changes["interval"] = _num(interval, cfg.interval, MIN_INTERVAL, MAX_INTERVAL)
    if sway_titles is not None:
        changes["sway_titles"] = sway_titles
    if shell_full_command is not None:
        changes["shell_full_command"] = shell_full_command
    return replace(cfg, **changes)
