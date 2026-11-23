# SPDX-License-Identifier: AGPL-3.0-or-later
"""The coordinator: runs the enabled observers and reports what is alive.

Each observer runs in its own daemon thread under a small supervisor (a crash is
logged and retried with backoff; an observer that says it is ``inactive`` or
``idle`` is not retried). The coordinator writes ``status.json`` next to the logs
every few seconds, which is what ``lhassa observe status`` reads. It acts on
nothing: it starts readers and writes history.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from lhassa.observe.config import OBSERVERS, ObserveConfig
from lhassa.observe.files import FilesObserver
from lhassa.observe.shell import ShellObserver, runtime_dir
from lhassa.observe.sway import SwayObserver
from lhassa.observe.system import SystemObserver
from lhassa.observe.writer import ObserveWriter, last_event_ts, observe_dir

STATUS_EVERY = 5.0
TERMINAL_STATES = frozenset({"inactive", "idle"})


def status_path(records_dir: str | Path, instance_id: str) -> Path:
    return observe_dir(records_dir, instance_id) / "status.json"


class Coordinator:
    def __init__(
        self,
        cfg: ObserveConfig,
        *,
        records_dir: str | Path,
        instance_id: str,
        shell_dir: Optional[Path] = None,
        proc_root: str | Path = "/proc",
        sys_root: str | Path = "/sys",
        swaysock: Optional[str] = None,
        clock: Callable[[], float] = time.time,
        status_every: float = STATUS_EVERY,
    ) -> None:
        self.status_every = status_every
        self.cfg = cfg
        self.records_dir = records_dir
        self.instance_id = instance_id
        self.shell_dir = shell_dir if shell_dir is not None else runtime_dir()
        self.proc_root, self.sys_root, self.swaysock = proc_root, sys_root, swaysock
        self._clock = clock
        self.state: dict[str, dict[str, str]] = {}
        self.writers: dict[str, ObserveWriter] = {}
        self._observers: dict[str, Any] = {}
        self._started = clock()
        self._lock = threading.Lock()

    def _setter(self, name: str) -> Callable[[str, str], None]:
        def set_state(state: str, note: str) -> None:
            with self._lock:
                self.state[name] = {"state": state, "note": note}

        return set_state

    def build(self) -> None:
        """Create the writers and observers for every enabled name."""
        for name in OBSERVERS:
            if name not in self.cfg.enabled:
                continue
            w = ObserveWriter(name, self.records_dir, self.instance_id, clock=self._clock)
            self.writers[name] = w
            on = self._setter(name)
            self.state[name] = {"state": "starting", "note": ""}
            if name == "system":
                obs: Any = SystemObserver(
                    w, interval=self.cfg.interval, proc_root=self.proc_root,
                    sys_root=self.sys_root, on_state=on,
                )
            elif name == "sway":
                obs = SwayObserver(w, titles=self.cfg.sway_titles, socket_path=self.swaysock, on_state=on)
            elif name == "shell":
                obs = ShellObserver(
                    w, directory=self.shell_dir, full_command=self.cfg.shell_full_command, on_state=on
                )
            else:
                obs = FilesObserver(
                    w, list(self.cfg.folders), max_watches=self.cfg.max_watches,
                    exclude=self.cfg.exclude, on_state=on,
                )
            self._observers[name] = obs

    def _supervise(self, name: str, stop: threading.Event) -> None:
        obs = self._observers[name]
        delay = 2.0
        while not stop.is_set():
            try:
                obs.run(stop)
            except Exception as exc:  # an observer must never take the process down
                self._setter(name)("failed", f"{type(exc).__name__}: {exc}"[:200])
                logger.warning(f"[observe] {name} crashed: {exc}")
            if stop.is_set() or self.state.get(name, {}).get("state") in TERMINAL_STATES:
                return
            stop.wait(delay)
            delay = min(60.0, delay * 2)

    def snapshot(self, *, running: bool = True) -> dict[str, Any]:
        observers: dict[str, Any] = {}
        for name in OBSERVERS:
            w = self.writers.get(name)
            st = self.state.get(name)
            if st is None:
                observers[name] = {"state": "off"}
                continue
            observers[name] = {
                **st,
                "events": w.events if w else 0,
                "dropped": w.dropped if w else 0,
                "last_event_ts": w.last_ts if w else None,
            }
        return {
            "pid": os.getpid(),
            "running": running,
            "started": self._started,
            "updated": self._clock(),
            "problems": list(self.cfg.problems),
            "observers": observers,
        }

    def write_status(self, *, running: bool = True) -> None:
        path = status_path(self.records_dir, self.instance_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(self.snapshot(running=running), fh, sort_keys=True)
            os.replace(tmp, path)
        except OSError as exc:
            logger.debug(f"[observe] status not written: {exc}")

    def run(self, stop: Optional[threading.Event] = None, *, duration: Optional[float] = None) -> None:
        """Run until ``stop`` is set (or ``duration`` seconds pass). Blocks."""
        stop = stop or threading.Event()
        self.build()
        threads = [
            threading.Thread(target=self._supervise, args=(n, stop), name=f"observe-{n}", daemon=True)
            for n in self._observers
        ]
        for t in threads:
            t.start()
        deadline = None if duration is None else time.monotonic() + duration
        try:
            while not stop.is_set():
                self.write_status()
                if deadline is not None and time.monotonic() >= deadline:
                    break
                stop.wait(self.status_every if deadline is None else min(self.status_every, 0.5))
        finally:
            stop.set()
            for t in threads:
                t.join(timeout=3.0)
            for w in self.writers.values():
                w.close()
            self.write_status(running=False)


def _alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def read_status(records_dir: str | Path, instance_id: str) -> dict[str, Any]:
    """What ``lhassa observe status`` shows: the status file, checked against reality."""
    try:
        data = json.loads(status_path(records_dir, instance_id).read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not data:
        return {"running": False, "observers": {}, "note": "no coordinator has run yet"}
    data["running"] = bool(data.get("running")) and _alive(data.get("pid"))
    for name, row in (data.get("observers") or {}).items():
        if isinstance(row, dict) and row.get("state") != "off" and not row.get("last_event_ts"):
            row["last_event_ts"] = last_event_ts(records_dir, instance_id, name)
    return data
