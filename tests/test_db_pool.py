# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the freelist connection pool in lhassa._vendor.aiosqlite.

Covers the exact invariants that make pooling safe under the "do-not-break"
bar: connection reuse, rollback-on-checkin (a normal close drops uncommitted
work), state reset (row_factory / foreign_keys must not leak to the next
borrower), non-crossover under concurrency, the idle bound, ``:memory:``
isolation, and the optional threaded offload mode.
"""

import asyncio
import sqlite3

import pytest

from lhassa._vendor import aiosqlite


@pytest.mark.asyncio
async def test_reuses_same_connection(tmp_path):
    path = tmp_path / "a.db"
    c1 = await aiosqlite.connect(path)
    raw1 = c1._conn
    await c1.close()
    c2 = await aiosqlite.connect(path)
    assert c2._conn is raw1
    await c2.close()


@pytest.mark.asyncio
async def test_str_and_path_share_pool(tmp_path):
    path = tmp_path / "b.db"
    c1 = await aiosqlite.connect(str(path))
    raw1 = c1._conn
    await c1.close()
    c2 = await aiosqlite.connect(path)
    assert c2._conn is raw1
    await c2.close()


@pytest.mark.asyncio
async def test_memory_never_pooled():
    c1 = await aiosqlite.connect(":memory:")
    raw1 = c1._conn
    await c1.close()
    c2 = await aiosqlite.connect(":memory:")
    assert c2._conn is not raw1
    await c2.close()


@pytest.mark.asyncio
async def test_rollback_on_checkin(tmp_path):
    path = tmp_path / "c.db"
    c1 = await aiosqlite.connect(path)
    await c1.execute("CREATE TABLE t(a)")
    await c1.execute("INSERT INTO t VALUES(42)")
    # No commit; a plain close must drop the uncommitted write (sqlite close
    # semantics), so the next borrower sees a clean database.
    await c1.close()
    c2 = await aiosqlite.connect(path)
    row = await (await c2.execute("SELECT count(*) FROM t")).fetchone()
    assert row[0] == 0
    await c2.close()


@pytest.mark.asyncio
async def test_row_factory_reset_on_checkin(tmp_path):
    path = tmp_path / "d.db"
    c1 = await aiosqlite.connect(path)
    await c1.execute("CREATE TABLE t(a)")
    await c1.execute("INSERT INTO t VALUES(1)")
    await c1.commit()
    c1.row_factory = aiosqlite.Row
    await c1.close()
    c2 = await aiosqlite.connect(path)
    assert c2.row_factory is None
    await c2.close()


@pytest.mark.asyncio
async def test_foreign_keys_off_after_checkin(tmp_path):
    path = tmp_path / "e.db"
    c1 = await aiosqlite.connect(path)
    await c1.execute("PRAGMA foreign_keys=ON")
    await c1.close()
    c2 = await aiosqlite.connect(path)
    row = await (await c2.execute("PRAGMA foreign_keys")).fetchone()
    assert row[0] == 0
    await c2.close()


@pytest.mark.asyncio
async def test_committed_write_survives_checkin(tmp_path):
    path = tmp_path / "ci.db"
    c1 = await aiosqlite.connect(path)
    await c1.execute("CREATE TABLE t(a)")
    await c1.execute("INSERT INTO t VALUES(7)")
    await c1.commit()
    await c1.close()
    c2 = await aiosqlite.connect(path)
    row = await (await c2.execute("SELECT a FROM t")).fetchone()
    assert row[0] == 7
    await c2.close()


@pytest.mark.asyncio
async def test_concurrent_coroutines_do_not_cross(tmp_path):
    path = tmp_path / "f.db"
    c1 = await aiosqlite.connect(path)
    await c1.execute("CREATE TABLE t(a)")
    await c1.commit()
    await c1.close()

    async def worker(n: int) -> int:
        conn = await aiosqlite.connect(path)
        try:
            await conn.execute("INSERT INTO t VALUES(?)", (n,))
            await conn.commit()
            row = await (await conn.execute("SELECT a FROM t WHERE a=?", (n,))).fetchone()
            return int(row[0])
        finally:
            await conn.close()

    results = await asyncio.gather(*(worker(i) for i in range(8)))
    assert sorted(results) == list(range(8))


@pytest.mark.asyncio
async def test_idle_bound_actually_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(aiosqlite, "_POOL_MAX_IDLE", 1)
    path = tmp_path / "g.db"
    c1 = await aiosqlite.connect(path)
    c2 = await aiosqlite.connect(path)
    raw1, raw2 = c1._conn, c2._conn
    await c1.close()
    await c2.close()  # pool already holds 1 idle -> this one is really closed
    c3 = await aiosqlite.connect(path)
    assert c3._conn is raw1
    with pytest.raises(sqlite3.ProgrammingError):
        raw2.execute("SELECT 1")
    await c3.close()


@pytest.mark.asyncio
async def test_pool_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(aiosqlite, "_pool_enabled", lambda: False)
    path = tmp_path / "h.db"
    c1 = await aiosqlite.connect(path)
    raw1 = c1._conn
    await c1.close()
    c2 = await aiosqlite.connect(path)
    assert c2._conn is not raw1
    await c2.close()


@pytest.mark.asyncio
async def test_busy_pool_creates_new_connection(tmp_path):
    path = tmp_path / "i.db"
    c1 = await aiosqlite.connect(path)
    raw1 = c1._conn
    c2 = await aiosqlite.connect(path)
    assert c2._conn is not raw1  # both held concurrently -> distinct
    await c2.close()
    await c1.close()


@pytest.mark.asyncio
async def test_pool_discards_connection_after_file_replaced(tmp_path):
    path = tmp_path / "stale.db"
    c1 = await aiosqlite.connect(path)
    await c1.execute("CREATE TABLE t(a)")
    await c1.execute("INSERT INTO t VALUES(1)")
    await c1.commit()
    raw1 = c1._conn
    await c1.close()
    # Simulate a restore/recreate: the file is deleted while the process runs.
    path.unlink()
    c2 = await aiosqlite.connect(path)  # must NOT reuse the stale connection
    assert c2._conn is not raw1
    await c2.execute("CREATE TABLE t(a)")
    await c2.commit()
    row = await (await c2.execute("SELECT count(*) FROM t")).fetchone()
    assert row[0] == 0
    await c2.close()


# ── threaded offload mode (LHASSA_DB_OFFLOAD) ────────────────────────────────


@pytest.mark.asyncio
async def test_threaded_mode_basic(tmp_path, monkeypatch):
    monkeypatch.setattr(aiosqlite, "_threaded", lambda: True)
    path = tmp_path / "t.db"
    c1 = await aiosqlite.connect(path)
    await c1.execute("CREATE TABLE t(a)")
    await c1.execute("INSERT INTO t VALUES(1)")
    await c1.commit()
    row = await (await c1.execute("SELECT a FROM t")).fetchone()
    assert row[0] == 1
    await c1.close()


@pytest.mark.asyncio
async def test_threaded_mode_concurrent(tmp_path, monkeypatch):
    monkeypatch.setattr(aiosqlite, "_threaded", lambda: True)
    path = tmp_path / "t2.db"
    c1 = await aiosqlite.connect(path)
    await c1.execute("CREATE TABLE t(a)")
    await c1.commit()
    await c1.close()

    async def worker(n: int) -> int:
        conn = await aiosqlite.connect(path)
        try:
            await conn.execute("INSERT INTO t VALUES(?)", (n,))
            await conn.commit()
            row = await (await conn.execute("SELECT a FROM t WHERE a=?", (n,))).fetchone()
            return int(row[0])
        finally:
            await conn.close()

    results = await asyncio.gather(*(worker(i) for i in range(16)))
    assert sorted(results) == list(range(16))


@pytest.mark.asyncio
async def test_threaded_mode_rollback_on_checkin(tmp_path, monkeypatch):
    monkeypatch.setattr(aiosqlite, "_threaded", lambda: True)
    path = tmp_path / "t3.db"
    c1 = await aiosqlite.connect(path)
    await c1.execute("CREATE TABLE t(a)")
    await c1.execute("INSERT INTO t VALUES(99)")
    await c1.close()  # uncommitted -> rolled back on checkin
    c2 = await aiosqlite.connect(path)
    row = await (await c2.execute("SELECT count(*) FROM t")).fetchone()
    assert row[0] == 0
    await c2.close()
