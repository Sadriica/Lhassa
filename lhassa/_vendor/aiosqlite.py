"""Tiny async-compatible sqlite3 wrapper used by Lhassa.

The upstream aiosqlite package uses a worker thread. In this deployment
environment that worker can stall before executing queued operations, which
hangs tests and startup. Lhassa only needs a small subset of the API, so this
module provides that subset over sqlite3 with async-shaped methods.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

Error = sqlite3.Error
Row = sqlite3.Row


class Cursor:
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    async def fetchone(self):
        return self._cursor.fetchone()

    async def fetchall(self):
        return self._cursor.fetchall()

    async def close(self) -> None:
        self._cursor.close()

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
            raw = (
                self._conn._conn.executemany(self._sql, self._params)
                if self._many
                else self._conn._conn.execute(self._sql, self._params)
            )
            self._cursor = Cursor(raw)
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
        self._conn = sqlite3.connect(path, uri=uri, **kwargs)

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        self._conn.row_factory = value

    def execute(self, sql: str, parameters: Any = None) -> _ExecuteProxy:
        return _ExecuteProxy(self, sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any) -> _ExecuteProxy:
        return _ExecuteProxy(self, sql, seq_of_parameters, many=True)

    async def execute_fetchall(self, sql: str, parameters: Any = None):
        cur = await self.execute(sql, parameters)
        try:
            return await cur.fetchall()
        finally:
            await cur.close()

    async def commit(self) -> None:
        self._conn.commit()

    async def rollback(self) -> None:
        self._conn.rollback()

    async def close(self) -> None:
        self._conn.close()

    async def backup(self, target: "Connection") -> None:
        self._conn.backup(target._conn)

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
    return Connection(database, uri=uri, **kwargs)
