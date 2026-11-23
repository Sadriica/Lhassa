# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run recorder: the harness adapter over the append-only event log.

A harness stream used to be consumed and thrown away (``runner._stream`` kept
only a broadcast and a running cost). The recorder keeps what happened, one
JSON line per event, written the moment it is parsed, so a run that dies, hits a
limit or is killed leaves a log that says how far it got. It is observation
only: nothing here changes how a job runs, and a failing recorder never stops
one.

The log itself (append-only, crash-safe, deterministic, ``0600``) is
:mod:`lhassa.record`; this module maps a parsed :class:`RunEvent` onto it:

* **Metadata by default.** A line carries kind, tool, target and cost. The event
  text is written only with ``content=True`` (and then scrubbed of PII and
  truncated).
* **Never-keep, fail-closed.** A target or text that touches a never-keep path
  (:mod:`lhassa.record.never_keep`) is withheld: the line says something
  happened, not what.
* **Opt-in ledger emission.** :class:`LedgerSink` mirrors the events that matter
  (tool calls, costs, errors, limits) into agent-ledger when that package is
  importable. It is not a dependency: absent, the sink does nothing.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from loguru import logger

from lhassa.harness.adapter import RunEvent
from lhassa.record import (
    WITHHELD, EventLog, Sink, log_path, read_log, withheld_rule, withheld_rule_in_text,
)

_TEXT_MAX = 2000

__all__ = ["LedgerSink", "RunRecorder", "log_path", "open_recorder", "read_log", "record_kind"]


def record_kind(ev: RunEvent) -> str:
    """The kind a recorded line carries: a line holding a tool call is a ``tool``."""
    return "tool" if ev.tool else ev.kind


def _scrub(text: str) -> str:
    from lhassa.privacy import pseudonymize

    try:
        return pseudonymize(text)[0]
    except Exception:
        return ""


class RunRecorder:
    """Records the events of one harness run. Never raises."""

    def __init__(
        self,
        path: str | Path,
        *,
        run_id: str,
        harness: str,
        content: bool = False,
        clock: Callable[[], float] = time.time,
        fsync_every: int = 50,
        sinks: Iterable[Sink] = (),
    ) -> None:
        self.harness = harness
        self.content = content
        self.log = EventLog(
            path, run_id=run_id, source=harness, clock=clock,
            fsync_every=fsync_every, sinks=sinks,
        )
        self.log.emit({"t": "start", "content": self.content})

    @property
    def path(self) -> Path:
        return self.log.path

    def record(self, ev: RunEvent) -> None:
        """Record one parsed harness event."""
        try:
            obj: dict[str, Any] = {"t": record_kind(ev)}
            if ev.tool:
                obj["tool"] = ev.tool
            rule = withheld_rule(ev.target) or (
                withheld_rule_in_text(ev.payload) if self.content else None
            )
            if ev.target:
                obj["target"] = WITHHELD if rule else ev.target[:300]
            if rule:
                obj["withheld"] = rule
            if ev.cost_usd is not None:
                obj["cost_usd"] = ev.cost_usd
            if self.content and ev.payload and not rule:
                obj["text"] = _scrub(ev.payload)[:_TEXT_MAX]
            self.log.emit(obj)
        except Exception as exc:  # fail closed: drop the line, keep the run
            logger.debug(f"[recorder] dropped an event: {exc}")

    def close(self, estado: str = "") -> None:
        """Write the closing line and release the file. Safe to call twice."""
        if self.log._fd is None:
            return
        self.log.emit({"t": "end", "estado": estado})
        self.log.close()


def open_recorder(instance_id: str, run_id: str, harness: str) -> Optional[RunRecorder]:
    """A recorder for one run, or ``None`` when recording is off or cannot start.

    Reads the ``record_*`` settings from :class:`Config` at call time. Never raises:
    the history is observation, so a failure here must not stop a job.
    """
    try:
        from lhassa.config import Config

        if not Config.record_runs:
            return None
        sinks: list[Sink] = []
        if Config.record_ledger:
            sinks.append(LedgerSink())
        return RunRecorder(
            log_path(Config.records_dir, instance_id, run_id),
            run_id=run_id, harness=harness, content=Config.record_content, sinks=sinks,
        )
    except Exception as exc:
        logger.warning(f"[recorder] disabled for run {run_id}: {exc}")
        return None


# ── agent-ledger sink (optional) ─────────────────────────────────────────────


class LedgerSink:
    """Mirror recorded events into agent-ledger when it is importable.

    Events are mapped onto the ledger's four kinds and carry a deterministic id
    (a hash of run and sequence), so re-emitting a run is idempotent
    (``INSERT OR IGNORE`` on the ledger side). ``text`` and ``start`` lines are
    not emitted: the ledger is for what cost money, touched something or failed.
    """

    HOST = "lhassa"

    def __init__(self, store: Any = None, *, db_path: Optional[str] = None) -> None:
        self._store = store
        self._db_path = db_path
        self._mod: Any = None
        self._broken = False

    def _load(self) -> bool:
        if self._store is not None and self._mod is not None:
            return True
        if self._broken:
            return False
        try:
            from agent_ledger import event as ev_mod  # type: ignore[import-not-found]

            self._mod = ev_mod
            if self._store is None:
                from agent_ledger.store import Store  # type: ignore[import-not-found]

                self._store = Store(self._db_path) if self._db_path else Store()
            return True
        except Exception as exc:
            self._broken = True
            logger.debug(f"[recorder] agent-ledger unavailable ({exc}); sink disabled")
            return False

    def __call__(self, rec: dict) -> None:
        kind = rec.get("t")
        if kind not in ("tool", "cost", "error", "limit") or not self._load():
            return
        m = self._mod
        common = dict(
            host=self.HOST,
            id=hashlib.sha256(f"{rec['run']}:{rec['seq']}".encode()).hexdigest()[:32],
            ts=float(rec["ts"]),
            agent=str(rec.get("source", "")),
            session=str(rec["run"]),
            turn=str(rec["seq"]),
        )
        try:
            if kind == "tool":
                event = m.AgentEvent(
                    kind=m.KIND_TOOL_CALL, tool=str(rec.get("tool", "")),
                    target=str(rec.get("target", "")), **common,
                )
            elif kind == "cost":
                cost = rec.get("cost_usd")
                event = m.AgentEvent(
                    kind=m.KIND_LLM_CALL, cost_usd=cost,
                    cost_source=m.COST_FROM_HOST if cost is not None else m.COST_UNKNOWN,
                    **common,
                )
            else:  # error | limit
                event = m.AgentEvent(
                    kind=m.KIND_NOTE, outcome=m.OUTCOME_ERROR,
                    detail={"type": kind}, **common,
                )
            self._store.record(event)
        except Exception as exc:
            logger.debug(f"[recorder] ledger emit failed: {exc}")
