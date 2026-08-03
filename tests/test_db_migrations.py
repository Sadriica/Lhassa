"""Versioned migration tests for the grande SQLite database."""

import pytest

from lhassa._vendor import aiosqlite
from lhassa.core.migrations import (
    Migration,
    MigrationValidationError,
    UnsupportedSchemaVersion,
    apply_migrations,
    get_schema_version,
)


@pytest.mark.asyncio
async def test_fresh_install_is_stamped_and_includes_knowledge(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    async with aiosqlite.connect(db.DB_PATH) as conn:
        assert await get_schema_version(conn) == db.SCHEMA_VERSION
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
    assert {
        "episodes",
        "tasks",
        "facts",
        "knowledge",
        "idea_assessments",
        "idea_assessment_events",
        "conformance_runs",
        "conformance_checks",
        "conformance_findings",
        "conformance_state",
    }.issubset(tables)

    await db.init_db()
    async with aiosqlite.connect(db.DB_PATH) as conn:
        assert await get_schema_version(conn) == db.SCHEMA_VERSION


@pytest.mark.asyncio
async def test_unversioned_legacy_upgrade_preserves_rows(tmp_path, monkeypatch):
    import lhassa.core.db as db

    path = tmp_path / "legacy.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    async with aiosqlite.connect(path) as conn:
        await conn.execute("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
                accion TEXT NOT NULL, objetivo TEXT, params TEXT,
                origen_trust INTEGER, origen_actor TEXT,
                estado TEXT NOT NULL DEFAULT 'pendiente',
                firma TEXT, creado_en TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, pid INTEGER,
                harness TEXT, estado TEXT NOT NULL DEFAULT 'running',
                costo_usd REAL DEFAULT 0.0, creado_en TEXT NOT NULL,
                terminado_en TEXT
            )
        """)
        await conn.execute(
            "INSERT INTO tasks(id, instance_id, accion, estado, creado_en) "
            "VALUES('legacy-task', 'inst', 'sync_repo', 'pendiente', '2026-01-01')"
        )
        await conn.commit()

    await db.init_db()

    async with aiosqlite.connect(path) as conn:
        assert await get_schema_version(conn) == db.SCHEMA_VERSION
        async with conn.execute("SELECT id FROM tasks") as cur:
            assert await cur.fetchone() == ("legacy-task",)
        async with conn.execute("PRAGMA table_info(tasks)") as cur:
            task_columns = {row[1] for row in await cur.fetchall()}
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge'"
        ) as cur:
            knowledge = await cur.fetchone()
    assert {"due_at", "recurrence", "updated_en"}.issubset(task_columns)
    assert knowledge == ("knowledge",)


@pytest.mark.asyncio
async def test_too_new_database_is_refused_without_modification(tmp_path, monkeypatch):
    import lhassa.core.db as db

    path = tmp_path / "future.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    future_version = db.SCHEMA_VERSION + 1
    async with aiosqlite.connect(path) as conn:
        await conn.execute("CREATE TABLE sentinel(value TEXT)")
        await conn.execute("INSERT INTO sentinel VALUES('keep')")
        await conn.execute(f"PRAGMA user_version = {future_version}")
        await conn.commit()

    with pytest.raises(UnsupportedSchemaVersion):
        await db.init_db()

    async with aiosqlite.connect(path) as conn:
        assert await get_schema_version(conn) == future_version
        async with conn.execute("SELECT value FROM sentinel") as cur:
            assert await cur.fetchone() == ("keep",)
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge'"
        ) as cur:
            assert await cur.fetchone() is None
        async with conn.execute("PRAGMA journal_mode") as cur:
            assert (await cur.fetchone())[0].lower() == "delete"


@pytest.mark.asyncio
async def test_failed_migration_rolls_back_ddl_and_does_not_stamp(tmp_path):
    path = tmp_path / "failure.db"

    async def already_applied(_conn):
        return None

    async def fail_after_ddl(conn):
        await conn.execute("CREATE TABLE partial(value TEXT)")
        raise RuntimeError("boom")

    migrations = (
        Migration(1, "already-applied", already_applied),
        Migration(2, "failing", fail_after_ddl),
    )
    async with aiosqlite.connect(path) as conn:
        await conn.execute("PRAGMA user_version = 1")
        await conn.commit()
        with pytest.raises(RuntimeError, match="boom"):
            await apply_migrations(conn, migrations)
        assert await get_schema_version(conn) == 1
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='partial'"
        ) as cur:
            assert await cur.fetchone() is None


@pytest.mark.asyncio
async def test_malformed_legacy_schema_is_not_stamped(tmp_path, monkeypatch):
    import lhassa.core.db as db

    path = tmp_path / "malformed.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    async with aiosqlite.connect(path) as conn:
        # Valid enough for the additive legacy operations, but missing `firma`.
        await conn.execute("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
                accion TEXT NOT NULL, objetivo TEXT, params TEXT,
                origen_trust INTEGER, origen_actor TEXT,
                estado TEXT NOT NULL DEFAULT 'pendiente', creado_en TEXT NOT NULL
            )
        """)
        await conn.commit()

    with pytest.raises(MigrationValidationError, match="tasks: missing firma"):
        await db.init_db()

    async with aiosqlite.connect(path) as conn:
        assert await get_schema_version(conn) == 0
        async with conn.execute("PRAGMA table_info(tasks)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge'"
        ) as cur:
            assert await cur.fetchone() is None
    assert "firma" not in columns
    assert "due_at" not in columns
