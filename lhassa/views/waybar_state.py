# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Waybar state file: a tiny runtime snapshot a status-bar widget can read.

The always-up process (``main.py``, ``lhassa serve``, ``docker/run_api.py``)
runs :func:`run_loop` as a background task. Every ``INTERVAL`` seconds, and
right after an approval is resolved (:func:`request_refresh`), it gathers the
status read-model (:mod:`lhassa.views.status`) and writes a small JSON file
atomically (temp file plus rename, mode 0600) to
``$XDG_RUNTIME_DIR/lhassa/waybar.json``. The widget script
(``scripts/waybar/lhassa-waybar``) only reads that file; it never imports
Lhassa, so it starts fast and works while the brain is down.

Location rules (the script mirrors them):

1. ``LHASSA_WAYBAR_STATE_FILE`` overrides everything.
2. ``$XDG_RUNTIME_DIR/lhassa/waybar.json`` when that directory exists.
3. Otherwise ``<tmpdir>/lhassa-<uid>/waybar.json`` (a private 0700 directory).

``LHASSA_WAYBAR_STATE=0`` (or ``off``/``false``/``no``) disables the writer.
Everything here is local and $0: no model, no network. Writing never raises into
the caller; a failure is logged at debug level and the loop keeps going. On a
clean shutdown the file is removed so the widget shows "no brain" at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: Seconds between periodic refreshes.
INTERVAL = 15.0

#: Bumped when the JSON shape changes incompatibly.
SCHEMA_VERSION = 1

DEFAULT_DASHBOARD_URL = "http://127.0.0.1:7766"

_OFF = {"0", "false", "off", "no"}

#: Set while :func:`run_loop` is running; :func:`request_refresh` pokes it.
_refresh: "asyncio.Event | None" = None


def enabled(env: "dict | None" = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("LHASSA_WAYBAR_STATE", "1")).strip().lower() not in _OFF


def state_path(env: "dict | None" = None) -> Path:
    """Where the state file lives (see the module docstring)."""
    env = os.environ if env is None else env
    override = (env.get("LHASSA_WAYBAR_STATE_FILE") or "").strip()
    if override:
        return Path(override)
    runtime = (env.get("XDG_RUNTIME_DIR") or "").strip()
    if runtime and os.path.isdir(runtime):
        return Path(runtime) / "lhassa" / "waybar.json"
    return Path(tempfile.gettempdir()) / f"lhassa-{os.getuid()}" / "waybar.json"


def dashboard_url(host: str = "", port: int = 7766) -> str:
    """The local dashboard URL; a wildcard bind maps to loopback."""
    host = (host or "127.0.0.1").strip()
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{int(port or 7766)}"


def _count(value: object) -> "int | None":
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def build_state(snap: dict, now: "float | None" = None, url: str = DEFAULT_DASHBOARD_URL) -> dict:
    """Pure: turn a :func:`lhassa.views.status.gather` snapshot into the file payload."""
    failing = snap.get("health_failing")
    return {
        "v": SCHEMA_VERSION,
        "written_at": float(time.time() if now is None else now),
        "instance_id": str(snap.get("instance_id") or ""),
        "cost_usd": round(float(snap.get("cost_usd") or 0.0), 4),
        "budget": int(snap.get("budget") or 0),
        "pct_used": round(float(snap.get("pct_used") or 0.0), 4),
        "soft_pct": float(snap.get("soft_pct") or 0.8),
        "approvals": _count(snap.get("approvals")),
        "tasks": _count(snap.get("tasks")),
        "beat_age": _count(snap.get("beat_age")),
        "health_failing": None if failing is None else [str(n) for n in failing],
        "dashboard_url": url,
    }


def write_state(payload: dict, path: "Path | None" = None) -> Path:
    """Atomically write ``payload`` as JSON (0600) to ``path``; return the path.

    The data goes to a sibling temp file created with ``O_EXCL`` and mode 0600,
    is flushed to disk, then renamed over the target, so a reader sees either
    the old file or the new one, never a partial write. The directory is
    created 0700.
    """
    path = Path(path) if path is not None else state_path()
    directory = path.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = directory / f".{path.name}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        tmp.unlink(missing_ok=True)  # a stale temp from a crashed write of this pid
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


async def refresh_once(path: "Path | None" = None) -> "Path | None":
    """Gather the snapshot and write it. Never raises; ``None`` on failure."""
    try:
        from lhassa.config import Config
        from lhassa.views import status

        snap = await status.gather()
        url = dashboard_url(getattr(Config, "api_host", ""), getattr(Config, "api_port", 7766))
        return write_state(build_state(snap, url=url), path)
    except Exception as exc:
        logger.debug("waybar state refresh failed: %s", exc)
        return None


def request_refresh() -> None:
    """Ask the running loop to refresh now (e.g. after an approval changed)."""
    if _refresh is not None:
        _refresh.set()


async def run_loop(interval: float = INTERVAL, path: "Path | None" = None) -> None:
    """Refresh periodically until cancelled; remove the file on the way out."""
    global _refresh
    if not enabled():
        return
    event = _refresh = asyncio.Event()
    try:
        while True:
            await refresh_once(path)
            try:
                await asyncio.wait_for(event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            event.clear()
    finally:
        _refresh = None
        try:
            (Path(path) if path is not None else state_path()).unlink(missing_ok=True)
        except OSError:
            pass
