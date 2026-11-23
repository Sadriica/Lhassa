# SPDX-License-Identifier: AGPL-3.0-or-later
"""Append-only storage.

The store lives inside someone else's process (a Hermes gateway, a hook that
a coding agent spawns), so it obeys three rules that a normal library does
not have to:

1. **It never raises into the host.** A ledger that crashes the agent it is
   auditing is worse than no ledger. `record()` returns False on failure and
   logs; it does not propagate.
2. **It has no third-party dependencies.** Anything imported here has to
   coexist with whatever the host already pinned. stdlib only.
3. **It never rewrites history.** There is no UPDATE and no DELETE in this
   module. A correction is a new event. (The one DDL exception is the additive
   `ALTER TABLE ... ADD COLUMN` of a schema migration, which touches no row.)
4. **It fails closed on privacy.** `record()` runs the never-keep policy
   (`policy.py`) first; an event that touches a protected path is stored with
   its payload withheld, and an event whose policy check fails is withheld too.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .event import AgentEvent
from .policy import REASON_POLICY_ERROR, WITHHELD_TARGET, Policy

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(os.environ.get("AGENT_LEDGER_DB", Path.home() / ".agent-ledger" / "ledger.db"))

SCHEMA_VERSION = 2

# Added in v2. Nullable-by-default columns (NOT NULL DEFAULT ''), so a writer
# that still lists only the v1 columns keeps working against a migrated file.
_V2_COLUMNS = (
    ("instance_id", "TEXT NOT NULL DEFAULT ''"),
    ("machine_id", "TEXT NOT NULL DEFAULT ''"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id                 TEXT PRIMARY KEY,
    ts                 REAL NOT NULL,
    host               TEXT NOT NULL,
    kind               TEXT NOT NULL,
    agent              TEXT NOT NULL DEFAULT '',
    session            TEXT NOT NULL DEFAULT '',
    turn               TEXT NOT NULL DEFAULT '',
    provider           TEXT NOT NULL DEFAULT '',
    model              TEXT NOT NULL DEFAULT '',
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL,
    cost_source        TEXT NOT NULL DEFAULT 'unknown',
    tool               TEXT NOT NULL DEFAULT '',
    target             TEXT NOT NULL DEFAULT '',
    outcome            TEXT NOT NULL DEFAULT 'ok',
    duration_ms        INTEGER NOT NULL DEFAULT 0,
    detail             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_host_ts ON events(host, ts);
CREATE INDEX IF NOT EXISTS idx_events_outcome ON events(outcome, ts);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_COLUMNS = (
    "id", "ts", "host", "kind", "agent", "session", "turn", "instance_id", "machine_id",
    "provider", "model",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "cost_usd", "cost_source", "tool", "target", "outcome",
    "duration_ms", "detail",
)

_INSERT = (
    f"INSERT OR IGNORE INTO events ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)


class Store:
    """A ledger on disk. Cheap to construct, safe to share across threads."""

    def __init__(self, path: Path | str | None = None, policy: Policy | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self.policy = policy if policy is not None else Policy.from_env()
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # -- connection handling -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        # One connection per thread: sqlite3 objects are not shareable, and a
        # host may call us from its main loop and a daemon thread at once.
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            # WAL lets a reader (the CLI, a dashboard) run while the host writes.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        conn.executescript(_SCHEMA)
        self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring a v1 file up to v2 in place. Idempotent and safe to race."""
        have = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        for name, ddl in _V2_COLUMNS:
            if name in have:
                continue
            try:
                conn.execute(f"ALTER TABLE events ADD COLUMN {name} {ddl}")
            except sqlite3.OperationalError as exc:
                # Another process migrated between our check and our ALTER.
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value "
            "WHERE CAST(value AS INTEGER) < CAST(excluded.value AS INTEGER)",
            (str(SCHEMA_VERSION),),
        )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- writing -------------------------------------------------------------

    def record(self, event: AgentEvent) -> bool:
        """Append one event. Returns False instead of raising, always.

        Re-recording the same id is a no-op, so a host that retries a hook
        cannot double-count a charge.
        """
        try:
            event = self._apply_policy(event)
            row = (
                event.id, event.ts, event.host, event.kind, event.agent, event.session,
                event.turn, event.instance_id, event.machine_id, event.provider, event.model, event.input_tokens,
                event.output_tokens, event.cache_read_tokens, event.cache_write_tokens,
                event.reasoning_tokens, event.cost_usd, event.cost_source, event.tool,
                event.target, event.outcome, event.duration_ms,
                json.dumps(event.detail, default=str)[:50_000],
            )
            self._connect().execute(_INSERT, row)
            return True
        except Exception:
            # Deliberately broad: the host's turn must survive our failure.
            logger.warning("agent-ledger: dropped an event", exc_info=True)
            return False

    def _apply_policy(self, event: AgentEvent) -> AgentEvent:
        """Withhold the payload of an event the never-keep policy covers.

        Fail closed: any error while evaluating counts as a match. The event
        still lands (something happened) but not what it touched.
        """
        try:
            reason = self.policy.evaluate(event)
        except Exception:
            logger.warning("agent-ledger: policy check failed; withholding", exc_info=True)
            reason = REASON_POLICY_ERROR
        if not reason:
            return event
        return replace(
            event, target=WITHHELD_TARGET, detail={"withheld": True, "reason": reason}
        )

    # -- reading -------------------------------------------------------------

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self._connect().execute(sql, tuple(params)))

    def events(
        self,
        *,
        since: Optional[float] = None,
        host: Optional[str] = None,
        limit: int = 100,
    ) -> Iterator[AgentEvent]:
        clauses, params = [], []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if host:
            clauses.append("host = ?")
            params.append(host)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.query(
            f"SELECT * FROM events {where} ORDER BY ts DESC LIMIT ?", (*params, limit)
        )
        for row in rows:
            data = dict(row)
            data["detail"] = json.loads(data["detail"] or "{}")
            yield AgentEvent(**data)

    def count(self) -> int:
        return self.query("SELECT COUNT(*) AS n FROM events")[0]["n"]
