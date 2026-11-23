# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one door every observer writes through.

An :class:`ObserveWriter` owns one observer's history: one JSONL file per day,
``<records_dir>/<instance>/observe/<observer>/<YYYY-MM-DD>.jsonl``, written by
:class:`lhassa.record.EventLog` (append-only, ``0600``, never raises).

Before an event reaches the log it passes the never-keep policy, fail-closed:

* a field declared as a path is checked with ``withheld_rule``;
* every string in the event is checked with ``withheld_rule_in_text``;
* a hit replaces the value by ``<withheld>`` and adds ``withheld: <rule>``, so the
  line keeps the fact that something happened and loses the what;
* any error while evaluating drops the event (counted, never raised).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from loguru import logger

from lhassa.record import WITHHELD, EventLog, withheld_rule, withheld_rule_in_text

SOURCE_PREFIX = "observe."


def observe_dir(records_dir: str | Path, instance_id: str) -> Path:
    return Path(records_dir).expanduser() / instance_id / "observe"


# ``withheld_rule`` resolves symlinks (about a millisecond per call). Observers repeat
# the same strings (a working directory, a watched root), so text verdicts are
# remembered briefly. The key carries the inputs the policy reads from the
# environment, so a changed HOME or rules file is never answered from the cache.
_CACHE_TTL = 30.0
_CACHE_MAX = 4096
_cache: dict[tuple[str, str, str, bool], tuple[float, Optional[str]]] = {}


def _cached_rule(value: str, as_path: bool) -> Optional[str]:
    now = time.monotonic()
    key = (os.environ.get("HOME", ""), os.environ.get("LHASSA_NEVER_KEEP_FILE", ""), value, as_path)
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL:
        return hit[1]
    verdict = withheld_rule(value) if as_path else withheld_rule_in_text(value)
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = (now, verdict)
    return verdict


def gate(
    event: dict[str, Any], *, paths: Iterable[str] = (), checked: Iterable[str] = ()
) -> Optional[dict[str, Any]]:
    """Apply never-keep to ``event``; ``None`` means drop it (could not evaluate).

    Fields named in ``paths`` are checked as paths. Every other string value is
    checked as free text. A withheld value becomes ``<withheld>``. Fields named in
    ``checked`` were already evaluated by the caller (the file observer checks the
    absolute path, which the relative one it stores cannot express) and are not
    evaluated again.
    """
    try:
        out = dict(event)
        path_keys, done = set(paths), set(checked)
        rule: Optional[str] = None
        for key, value in event.items():
            if not isinstance(value, str) or not value or key in done:
                continue
            hit = _cached_rule(value, True) if key in path_keys else None
            hit = hit or _cached_rule(value, False)
            if hit:
                out[key] = WITHHELD
                rule = rule or hit
        if rule:
            out["withheld"] = rule
        return out
    except Exception as exc:  # fail closed
        logger.debug(f"[observe] dropped an event: {exc}")
        return None


class ObserveWriter:
    """Daily-rolling, never-keep-gated event writer for one observer."""

    def __init__(
        self,
        observer: str,
        records_dir: str | Path,
        instance_id: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.observer = observer
        self.dir = observe_dir(records_dir, instance_id) / observer
        self._clock = clock
        self._day = ""
        self._log: Optional[EventLog] = None
        self.events = 0
        self.dropped = 0
        self.last_ts: Optional[float] = None

    def _open(self, ts: float) -> EventLog:
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        if self._log is not None and day == self._day:
            return self._log
        if self._log is not None:
            self._log.close()
        stamp = time.strftime("%H%M%S", time.localtime(ts))
        self._day = day
        self._log = EventLog(
            self.dir / f"{day}.jsonl",
            run_id=f"{self.observer}-{day.replace('-', '')}T{stamp}",
            source=SOURCE_PREFIX + self.observer,
            clock=self._clock,
            fsync_every=200,
        )
        return self._log

    def emit(
        self,
        kind: str,
        fields: Optional[dict[str, Any]] = None,
        *,
        paths: Iterable[str] = (),
        checked: Iterable[str] = (),
    ) -> Optional[dict]:
        """Gate and append one event of type ``kind``; never raises."""
        try:
            ts = self._clock()
            safe = gate({"t": kind, **(fields or {})}, paths=paths, checked=checked)
            if safe is None:
                self.dropped += 1
                return None
            full = self._open(ts).emit(safe)
            self.events += 1
            self.last_ts = ts
            return full
        except Exception as exc:
            self.dropped += 1
            logger.debug(f"[observe] {self.observer} write failed: {exc}")
            return None

    def close(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None


def last_event_ts(records_dir: str | Path, instance_id: str, observer: str) -> Optional[float]:
    """Timestamp of the newest event of ``observer`` on disk, or ``None``."""
    d = observe_dir(records_dir, instance_id) / observer
    try:
        files = sorted(d.glob("*.jsonl"))
    except OSError:
        return None
    for f in reversed(files):
        try:
            size = f.stat().st_size
            with open(f, "rb") as fh:
                fh.seek(max(0, size - 4096))
                tail = fh.read().split(b"\n")
        except OSError:
            continue
        for raw in reversed(tail):
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("ts"), (int, float)):
                return float(obj["ts"])
    return None
