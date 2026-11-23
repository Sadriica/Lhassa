"""Tiny async-compatible sqlite3 wrapper used by Lhassa.

The upstream aiosqlite package uses a worker thread. In this deployment
environment that worker can stall before executing queued operations, which
hangs tests and startup. Lhassa only needs a small subset of the API, so this
module provides that subset over sqlite3 with async-shaped methods.

Runtime model (two modes, chosen per-process via env at call time):

* **sync (default)** — every ``sqlite3`` call runs directly on the event-loop
  thread. This is the original behaviour. It is ideal for the heartbeat (a
  single-task process, so blocking the loop costs nothing) and for tests.
* **threaded (opt-in, ``LHASSA_DB_OFFLOAD=1``)** — sqlite3 calls run through
  ``asyncio.to_thread`` serialized by a ``threading.Lock`` per connection, so a
  long-lived async server (the dashboard API) never blocks its loop on DB I/O.
  Connections are created with ``check_same_thread=False``. The pool below
  guarantees a connection has exactly one holder at a time, so the per-connection
  lock only mediates between the loop thread and the worker thread of the SAME
  connection — never between coroutines (they hold different connections).

Connection reuse: ``connect(path)/close()`` backs a small **freelist pool**
keyed by resolved path, so consecutive operations on the same database reuse
one underlying connection (warm page cache + statement cache, no reconnect).
``close()`` is a *check-in*: it rolls back any open transaction, clears
``row_factory`` and turns ``foreign_keys`` back off so the next borrower gets
exactly the "fresh connection" semantics it would have had. Toggle with
``LHASSA_DB_POOL`` (default ``1``). ``:memory:`` databases are never pooled.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

Error = sqlite3.Error
Row = sqlite3.Row

_POOL_MAX_IDLE = 2
_POOLS: dict[str, list["Connection"]] = {}


def _pool_enabled() -> bool:
    """Freelist connection reuse lives behind LHASSA_DB_POOL (default on)."""
    return os.getenv("LHASSA_DB_POOL", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def _threaded() -> bool:
    """sqlite3-on-thread offload behind LHASSA_DB_OFFLOAD (default off)."""
    return os.getenv("LHASSA_DB_OFFLOAD", "0").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def _pool_key_for(database: str | Path, uri: bool) -> str | None:
    """A canonical per-file pool key, or None when the DB must not be pooled."""
    if uri:
        # URI strings can alias ":memory:" or open with exotic flags — never pool.
        return None
    text = str(database)
    if not text or text == ":memory:" or text.startswith("file:"):
        return None
    return str(Path(text).resolve())


def _ino_of(path: str) -> int | None:
    """``st_ino`` of a path, or None if it does not exist (sqlite will create it)."""
    try:
        return Path(path).stat().st_ino
    except OSError:
        return None


async def _call_threaded(lock: threading.Lock, fn, *args):
    """Run ``fn(*args)`` in the loop's executor under ``lock``."""

    def _locked():
        with lock:
            return fn(*args)

    return await asyncio.to_thread(_locked)


_DEFAULT_BUSY_BUDGET = 5.0  # sqlite3.connect's own default ``timeout``


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


async def _call_threaded_retry(lock: threading.Lock, budget: float, fn, *args):
    """``_call_threaded`` that waits out a busy database on the event loop.

    In threaded mode sqlite's own busy handler would sleep *inside* a worker
    thread of the loop's default executor. With at least as many contenders as
    that executor has threads (``cpu_count + 4``, so 8 on a 4-core runner) the
    waiters fill every thread and the one holding the write lock can never get
    a thread to commit: nothing moves until the timeout expires. The
    connection therefore runs with ``timeout=0`` and the wait happens here,
    where it does not hold a thread.
    """
    deadline = time.monotonic() + budget
    delay = 0.002
    while True:
        try:
            return await _call_threaded(lock, fn, *args)
        except sqlite3.OperationalError as exc:
            if not _is_busy(exc) or time.monotonic() >= deadline:
                raise
        await asyncio.sleep(delay)
        delay = min(delay * 2, 0.05)


class Cursor:
    def __init__(
        self,
        cursor: sqlite3.Cursor,
        *,
        lock: threading.Lock | None = None,
        budget: float = _DEFAULT_BUSY_BUDGET,
    ):
        self._cursor = cursor
        self._lock = lock
        self._budget = budget

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    async def fetchone(self):
        if self._lock is None:
            return self._cursor.fetchone()
        return await _call_threaded_retry(self._lock, self._budget, self._cursor.fetchone)

    async def fetchall(self):
        if self._lock is None:
            return self._cursor.fetchall()
        return await _call_threaded_retry(self._lock, self._budget, self._cursor.fetchall)

    async def close(self) -> None:
        if self._lock is None:
            self._cursor.close()
        else:
            await _call_threaded(self._lock, self._cursor.close)

    async def __aenter__(self) -> "Cursor":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


class _ExecuteProxy:
    def __init__(self, conn: "Connection", sql: str, params: Any = None, *, many: bool = False):
        self._conn = conn
        self._sql = sql
        self._params = () if params is None else params
        self._many = many
        self._cursor: Cursor | None = None

    async def _run(self) -> Cursor:
        if self._cursor is None:
            raw = await self._conn._run_raw(self._sql, self._params, self._many)
            self._cursor = Cursor(raw, lock=self._conn._lock, budget=self._conn._busy_budget)
        return self._cursor

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self) -> Cursor:
        return await self._run()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._cursor is not None:
            await self._cursor.close()


class Connection:
    def __init__(self, path: str | Path, *, uri: bool = False, **kwargs: Any):
        self._busy_budget = float(kwargs.get("timeout", _DEFAULT_BUSY_BUDGET))
        if _threaded():
            kwargs.setdefault("check_same_thread", False)
            kwargs["timeout"] = 0  # the busy wait is done by _call_threaded_retry
        self._conn = sqlite3.connect(path, uri=uri, **kwargs)
        self._lock = threading.Lock() if _threaded() else None
        self._pool_key: str | None = None
        self._checked_in = False
        # The inode of the .db file this connection was opened against (set by
        # connect()). A pooled connection to a file that was deleted/replaced
        # must not be reused (it would keep serving the OLD inode), so each
        # checkout compares this against the current file's inode.
        self._file_ino: int | None = None

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        if self._lock is None:
            self._conn.row_factory = value
        else:
            with self._lock:
                self._conn.row_factory = value

    def execute(self, sql: str, parameters: Any = None) -> _ExecuteProxy:
        return _ExecuteProxy(self, sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any) -> _ExecuteProxy:
        return _ExecuteProxy(self, sql, seq_of_parameters, many=True)

    async def _run_raw(self, sql: str, params: Any, many: bool):
        if self._lock is None:
            if many:
                return self._conn.executemany(sql, params)
            return self._conn.execute(sql, params)

        def _locked():
            if many:
                return self._conn.executemany(sql, params)
            return self._conn.execute(sql, params)

        return await _call_threaded_retry(self._lock, self._busy_budget, _locked)

    def _reset_raw(self) -> None:
        """Restore 'fresh connection' state before returning to the pool."""
        if self._conn.in_transaction:
            self._conn.rollback()
        self._conn.row_factory = None
        self._conn.execute("PRAGMA foreign_keys=OFF")

    async def execute_fetchall(self, sql: str, parameters: Any = None):
        cur = await self.execute(sql, parameters)
        try:
            return await cur.fetchall()
        finally:
            await cur.close()

    def _file_is_stale(self) -> bool:
        """True when the .db file this connection was opened against was
        deleted or replaced since (the connection still points at the old
        inode). A stale pooled connection must be discarded, never reused."""
        if self._pool_key is None:
            return False
        return _ino_of(self._pool_key) != self._file_ino

    def _discard(self) -> None:
        """Really release the underlying connection (no pool check-in).

        Safe to do synchronously from the loop thread: a pooled (idle)
        connection has no worker task in flight on it, and in threaded mode the
        connection was created with ``check_same_thread=False``.
        """
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    async def commit(self) -> None:
        if self._lock is None:
            self._conn.commit()
        else:
            await _call_threaded_retry(self._lock, self._busy_budget, self._conn.commit)

    async def rollback(self) -> None:
        if self._lock is None:
            self._conn.rollback()
        else:
            await _call_threaded(self._lock, self._conn.rollback)

    async def close(self) -> None:
        if self._checked_in:
            return
        self._checked_in = True
        if self._pool_key is not None and _pool_enabled():
            if self._lock is None:
                self._reset_raw()
            else:
                await _call_threaded(self._lock, self._reset_raw)
            stack = _POOLS.setdefault(self._pool_key, [])
            if len(stack) < _POOL_MAX_IDLE:
                stack.append(self)
                return
        # Not pooled (or pool full): really release the underlying connection.
        if self._lock is None:
            self._conn.close()
        else:
            await _call_threaded(self._lock, self._conn.close)

    async def backup(self, target: "Connection") -> None:
        if self._lock is None:
            self._conn.backup(target._conn)
        else:
            await _call_threaded(self._lock, self._conn.backup, target._conn)

    def __await__(self):
        async def _self():
            return self

        return _self().__await__()

    async def __aenter__(self) -> "Connection":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            await self.rollback()
        await self.close()


def connect(database: str | Path, *, uri: bool = False, **kwargs: Any) -> Connection:
    key = _pool_key_for(database, uri)
    if _pool_enabled() and key is not None and not kwargs:
        stack = _POOLS.get(key)
        while stack:
            conn = stack.pop()
            if conn._file_is_stale():
                conn._discard()  # file deleted/replaced since — never reuse
                continue
            conn._checked_in = False
            return conn
    conn = Connection(database, uri=uri, **kwargs)
    conn._pool_key = key
    conn._file_ino = _ino_of(key) if key is not None else None
    return conn