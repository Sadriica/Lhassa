# SPDX-License-Identifier: AGPL-3.0-or-later
"""Append-only, crash-safe JSONL event log: the contract the history is built on.

One file per source (a harness run, later an observer). Properties, each with a
test in ``tests/test_record_log.py``:

* **Append-only, one line per event.** The file is opened ``O_APPEND`` and each
  event is a single ``os.write``, so a process killed mid-run leaves complete
  lines plus at most one torn last line. :func:`read_log` returns the complete
  lines and reports the tear instead of raising.
* **Deterministic.** Keys are sorted, ``seq`` is the only counter and the clock
  is injectable: the same events with the same clock give the same bytes.
* **Private.** Files are created ``0600``.
* **Never raises.** A full disk or a bad sink is logged and the caller carries on.

``fsync`` runs on terminal events, on close and every ``fsync_every`` events. A
process kill never loses a ``write`` already made; ``fsync`` is for power loss.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from loguru import logger

SCHEMA = 1
TERMINAL = frozenset({"limit", "error", "done", "end"})
Sink = Callable[[dict], None]


def _line(obj: dict) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


class EventLog:
    """Writes the events of one source to ``path``."""

    def __init__(
        self,
        path: str | Path,
        *,
        run_id: str,
        source: str,
        clock: Callable[[], float] = time.time,
        fsync_every: int = 50,
        sinks: Iterable[Sink] = (),
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.source = source
        self._clock = clock
        self._fsync_every = max(1, fsync_every)
        self._sinks = list(sinks)
        self.seq = 0
        self._fd: Optional[int] = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except OSError as exc:
            logger.warning(f"[record] cannot open {self.path}: {exc}")

    def emit(self, obj: dict[str, Any]) -> dict:
        """Append one event (``obj`` must carry ``t``); returns the full line written."""
        self.seq += 1
        full = {
            "v": SCHEMA,
            "run": self.run_id,
            "source": self.source,
            "seq": self.seq,
            "ts": round(self._clock(), 3),
            **obj,
        }
        if self._fd is not None:
            try:
                os.write(self._fd, _line(full))
                if full.get("t") in TERMINAL or self.seq % self._fsync_every == 0:
                    self.sync()
            except OSError as exc:
                logger.warning(f"[record] write failed for {self.path}: {exc}")
        for sink in self._sinks:
            try:
                sink(full)
            except Exception as exc:  # a sink must never break the source
                logger.debug(f"[record] sink failed: {exc}")
        return full

    def sync(self) -> None:
        if self._fd is not None:
            try:
                os.fsync(self._fd)
            except OSError:
                pass

    def close(self) -> None:
        """Release the file. Safe to call twice."""
        if self._fd is None:
            return
        self.sync()
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None


def read_log(path: str | Path) -> tuple[list[dict], bool]:
    """Read a log: ``(events, torn)``.

    ``events`` are the complete, parseable lines in order. ``torn`` is True when
    the file ended in a partial line or held an unparseable one (a crash during a
    write). A missing file is an empty log, not an error.
    """
    events: list[dict] = []
    torn = False
    try:
        data = Path(path).read_bytes()
    except OSError:
        return events, torn
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    elif lines:
        torn = True  # no trailing newline: the last write did not finish
        lines.pop()
    for raw in lines:
        try:
            obj = json.loads(raw)
        except ValueError:
            torn = True
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events, torn


def log_path(records_dir: str | Path, instance_id: str, run_id: str) -> Path:
    """Where the log of ``run_id`` lives: derivable from the id, so no pointer to store."""
    return Path(records_dir).expanduser() / instance_id / f"{run_id}.jsonl"
