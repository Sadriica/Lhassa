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
Everything here is local and $0: no model, no network (the one exception is the
optional CI lookup through ``gh``, cached for five minutes and run in a worker
thread). Writing never raises into the caller; a failure is logged at debug
level and the loop keeps going. On a clean shutdown the file is removed so the
widget shows "no brain" at once.

Schema v2 adds, on top of every v1 key (a v1 reader keeps working, and a v2
reader degrades on a v1 file): ``class`` (the verdict), ``agents``,
``attention``, ``system``, ``today`` and ``lhassa``. Only numbers and repo or
workspace names go in (see :mod:`lhassa.views.waybar_collect`).

Quiet bar: the file is rewritten only when its *relevant* content changes
(temperature, CPU and battery are bucketed, the temperature class is latched
with hysteresis) or every :data:`HEARTBEAT_S` seconds, so the script's
staleness check still works and a burst of events does not make the bar flicker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path

from lhassa.views import waybar_collect

logger = logging.getLogger(__name__)

#: Seconds between periodic refreshes.
INTERVAL = 15.0

#: Bumped when the JSON shape changes incompatibly.
SCHEMA_VERSION = 2

DEFAULT_DASHBOARD_URL = "http://127.0.0.1:7766"

#: Rewrite an unchanged payload at least this often (the script treats a file
#: older than 60 s as stale).
HEARTBEAT_S = 30.0

#: Temperature classes as ``(enter at, leave below)`` in Celsius: a hysteresis
#: band so a package hovering at the edge does not flip the icon colour.
TEMP_WARN = (85.0, 80.0)
TEMP_ALERT = (100.0, 95.0)

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


def latch(level: str, temp: "float | None", warn=TEMP_WARN, alert=TEMP_ALERT) -> str:
    """Temperature class with hysteresis: enter at the high mark, leave below the low one."""
    if temp is None:
        return "ok"
    if temp >= alert[0] or (level == "alert" and temp >= alert[1]):
        return "alert"
    if temp >= warn[0] or (level in ("warn", "alert") and temp >= warn[1]):
        return "warn"
    return "ok"


#: Attention kinds that turn the icon red rather than yellow.
_ALERT_KINDS = frozenset({"review", "service"})


def build_attention(snap: dict, approvals: "list[dict] | None", collected: "dict | None") -> dict:
    """Everything that needs the owner, in priority order, each with its origin."""
    collected = collected or {}
    items: list[dict] = []
    for row in approvals or []:
        items.append({"kind": "approval", "id": str(row.get("id") or ""),
                      "label": str(row.get("label") or "approval")[:40], "source": "approvals"})
    if approvals is None and _count(snap.get("approvals")):
        items.extend({"kind": "approval", "id": "", "label": "approval", "source": "approvals"}
                     for _ in range(int(snap["approvals"])))
    items.extend(collected.get("pending") or [])
    items.extend(collected.get("critical") or [])
    items.extend(collected.get("pushes") or [])
    items.extend(collected.get("ci") or [])
    items.extend(collected.get("services") or [])
    alerting = set(snap.get("health_alert") or ())
    for name in snap.get("health_failing") or ():
        items.append({"kind": "health", "label": str(name)[:40], "source": "health",
                      "level": "alert" if name in alerting else "warn"})
    return {"count": min(len(items), 99), "items": items[:30]}


def verdict(payload: dict) -> str:
    """``ok`` / ``warn`` / ``alert`` for the icon, from a built payload."""
    items = (payload.get("attention") or {}).get("items") or []
    budget_on = (payload.get("budget") or 0) > 0
    pct = payload.get("pct_used") or 0.0
    temp = (payload.get("system") or {}).get("temp_level")
    if (payload.get("health_alert") or (budget_on and pct >= 1.0) or temp == "alert"
            or any(i.get("kind") in _ALERT_KINDS for i in items)):
        return "alert"
    if (items or payload.get("approvals") or payload.get("health_failing") or temp == "warn"
            or (budget_on and pct >= (payload.get("soft_pct") or 0.8))):
        return "warn"
    return "ok"


def build_state(snap: dict, now: "float | None" = None, url: str = DEFAULT_DASHBOARD_URL,
                extra: "dict | None" = None) -> dict:
    """Pure: turn a :func:`lhassa.views.status.gather` snapshot into the file payload.

    ``extra`` carries the v2 sections the writer collected (``agents``,
    ``attention``, ``system``, ``today``, ``lhassa``); missing ones get neutral
    defaults, so a bare snapshot still yields a valid v2 file.
    """
    extra = extra or {}
    failing = snap.get("health_failing")
    alerting = snap.get("health_alert")
    payload = {
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
        "health_alert": [] if alerting is None else [str(n) for n in alerting],
        "dashboard_url": url,
        "agents": extra.get("agents") or {"window_s": 300, "total": 0, "hosts": {}, "sessions": []},
        "attention": extra.get("attention") or {"count": 0, "items": []},
        "system": extra.get("system") or {
            "power_mode": "", "temp_c": None, "temp_level": "ok", "cpu_pct": None,
            "battery_pct": None, "charging": False, "suggestion": None},
        "today": extra.get("today") or {
            "events": 0, "commits": 0, "pushes": 0, "api_usd": 0.0, "top_repo": ""},
        "lhassa": extra.get("lhassa") or {"health": "", "digest_age_s": None, "model": ""},
    }
    payload["class"] = verdict(payload)
    return payload


def relevant_key(payload: dict) -> str:
    """The part of a payload that justifies a rewrite.

    Volatile numbers are bucketed (temperature to 5 C, CPU to 10 points,
    battery to 5, spend to a cent, event counts to 25) and the clock and
    heartbeat age are dropped, so only a change that matters moves the key.
    """
    p = json.loads(json.dumps(payload))
    p.pop("written_at", None)
    p.pop("beat_age", None)
    p["cost_usd"] = round(p.get("cost_usd") or 0.0, 2)
    system = p.get("system") or {}
    for key, step in (("temp_c", 5), ("cpu_pct", 10), ("battery_pct", 5)):
        if isinstance(system.get(key), (int, float)):
            system[key] = int(round(system[key] / step) * step)
    today = p.get("today") or {}
    today["api_usd"] = round(today.get("api_usd") or 0.0, 2)
    today["events"] = int((today.get("events") or 0) // 25)
    lh = p.get("lhassa") or {}
    lh["digest_age_s"] = None
    return json.dumps(p, sort_keys=True, separators=(",", ":"))


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


async def _pending_approvals(instance_id: str) -> "list[dict] | None":
    """Pending approvals as ``{id, label}`` (action names only), or ``None``."""
    try:
        from lhassa.core import db

        rows = await db.load_pending_approvals(instance_id)
        out = []
        for r in rows:
            payload = r.get("payload") or {}
            out.append({"id": r.get("id"), "label": payload.get("accion") or r.get("tipo") or "approval"})
        return out
    except Exception:
        return None


def _lhassa_section(now: float) -> dict:
    """Health digest level and age, and the local model when the seat is Ollama."""
    out = {"health": "", "digest_age_s": None, "model": ""}
    try:
        from lhassa.config import Config

        raw = json.loads((Path(getattr(Config, "health_dir", "") or Path.home() / ".lhassa" / "health")
                          / "digest.json").read_text(encoding="utf-8"))
        out["health"] = str(raw.get("worst_level") or "")[:12]
        if isinstance(raw.get("ts"), (int, float)):
            out["digest_age_s"] = max(0, int(now - raw["ts"]))
        if str(getattr(Config, "llm_provider", "")).lower() == "ollama":
            out["model"] = str(getattr(Config, "model", "") or "")[:40]
    except Exception:
        pass
    return out


class Publisher:
    """Builds the payload and writes it only when something relevant changed."""

    def __init__(self, ci: "waybar_collect.CiWatcher | None" = None,
                 clock=time.time, heartbeat: float = HEARTBEAT_S) -> None:
        self.ci = ci or waybar_collect.CiWatcher()
        self.cpu = waybar_collect.CpuMeter()
        self.clock = clock
        self.heartbeat = heartbeat
        self.temp_level = "ok"
        self._key: "str | None" = None
        self._wrote_at = 0.0

    def _collect_sync(self, snap: dict, now: float) -> dict:
        """The blocking reads, run in a worker thread."""
        c = waybar_collect
        ledger = c.collect_ledger(now=now)
        review = c.collect_review(now=now)
        temp = c.package_temp()
        pct, charging = c.battery()
        return {
            "ledger": ledger,
            "review": review,
            "pending": c.collect_pending(now=now),
            "services": c.failed_services(),
            "repos": c.configured_repos(cwds=ledger["cwds"]),
            "temp": temp,
            "cpu": self.cpu.sample(),
            "battery": (pct, charging),
            "power_mode": c.power_mode(),
        }

    def assemble(self, snap: dict, approvals: "list[dict] | None", got: dict, now: float) -> dict:
        ledger, review = got["ledger"], got["review"]
        self.temp_level = latch(self.temp_level, got.get("temp"))
        pct, charging = got.get("battery") or (None, False)
        extra = {
            "agents": ledger["agents"],
            "attention": build_attention(
                snap, approvals,
                {"pending": got.get("pending"), "critical": review["critical"],
                 "pushes": review["pushes"], "services": got.get("services"),
                 "ci": self.ci.failing(got.get("repos") or [])}),
            "system": {"power_mode": got.get("power_mode") or "", "temp_c": got.get("temp"),
                       "temp_level": self.temp_level, "cpu_pct": got.get("cpu"),
                       "battery_pct": pct, "charging": bool(charging),
                       "suggestion": None},  # reserved: Lhassa's profile suggestion
            "today": {**ledger["today"], "commits": review["commits"], "pushes": review["push_count"]},
            "lhassa": _lhassa_section(now),
        }
        return extra

    async def refresh(self, path: "Path | None" = None) -> "Path | None":
        """Gather, and write if relevant. Never raises; ``None`` on failure."""
        try:
            from lhassa.config import Config
            from lhassa.views import status

            now = self.clock()
            snap = await status.gather()
            approvals = await _pending_approvals(str(snap.get("instance_id") or ""))
            got = await asyncio.to_thread(self._collect_sync, snap, now)
            extra = self.assemble(snap, approvals, got, now)
            url = dashboard_url(getattr(Config, "api_host", ""), getattr(Config, "api_port", 7766))
            payload = build_state(snap, now=now, url=url, extra=extra)
            target = Path(path) if path is not None else state_path()
            key = relevant_key(payload)
            due = now - self._wrote_at >= self.heartbeat
            if key == self._key and not due and target.exists():
                return target
            written = write_state(payload, target)
            self._key, self._wrote_at = key, now
            return written
        except Exception as exc:
            logger.debug("waybar state refresh failed: %s", exc)
            return None


_publisher: "Publisher | None" = None


async def refresh_once(path: "Path | None" = None) -> "Path | None":
    """Refresh through the shared :class:`Publisher`. Never raises; ``None`` on failure."""
    global _publisher
    if _publisher is None:
        _publisher = Publisher()
    return await _publisher.refresh(path)


def request_refresh() -> None:
    """Ask the running loop to refresh now (e.g. after an approval changed)."""
    if _refresh is not None:
        _refresh.set()


async def run_loop(interval: float = INTERVAL, path: "Path | None" = None) -> None:
    """Refresh periodically until cancelled; remove the file on the way out."""
    global _refresh
    if not enabled():
        return
    global _publisher
    event = _refresh = asyncio.Event()
    _publisher = Publisher()  # a fresh quiet-bar memory per run
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
