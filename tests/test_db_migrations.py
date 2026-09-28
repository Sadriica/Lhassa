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


# ── migration 11: multiuser (actor_id + shared resources) ──────────────────────


@pytest.mark.asyncio
async def test_multiuser_migration_adds_actor_id_and_shared_tables(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    async with aiosqlite.connect(db.DB_PATH) as conn:
        for table in ("approvals", "facts", "knowledge"):
            async with conn.execute(f"PRAGMA table_info({table})") as cur:
                cols = {row[1] for row in await cur.fetchall()}
            assert "actor_id" in cols
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('shared_resources', 'resource_subscriptions')"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
    assert tables == {"shared_resources", "resource_subscriptions"}


@pytest.mark.asyncio
async def test_multiuser_migration_backfills_existing_rows(tmp_path, monkeypatch):
    """A pre-Phase-A database (schema version 10) with an existing approval,
    fact and knowledge row must upgrade WITHOUT losing data and WITHOUT ever
    leaving actor_id NULL: an approval recovers its actor from its JSON
    payload (Origin.actor_id already rode there); facts/knowledge default to
    the owner (Config.root_admin_id)."""
    import lhassa.core.db as db
    from lhassa.core.migrations import apply_migrations

    path = tmp_path / "pre-phase-a.db"
    monkeypatch.setattr(db, "DB_PATH", path)

    # Build a version-10 database by running every migration up to (not
    # including) migration 11, exactly the way a real pre-upgrade install
    # would look.
    migrations_10 = db.MIGRATIONS[:10]
    async with aiosqlite.connect(path) as conn:
        await apply_migrations(conn, migrations_10)
        await conn.execute(
            "INSERT INTO approvals(id, instance_id, tipo, payload, estado, creado_en) "
            "VALUES('ap-legacy', 'inst', 'mint_task', ?, 'pendiente', '2026-01-01')",
            ('{"actor_id": "carol", "accion": "sync_repo"}',),
        )
        await conn.execute(
            "INSERT INTO approvals(id, instance_id, tipo, payload, estado, creado_en) "
            "VALUES('ap-no-actor', 'inst', 'mint_task', '{}', 'pendiente', '2026-01-01')"
        )
        await conn.execute(
            "INSERT INTO facts(id, instance_id, tipo, contenido, pinned, tags, "
            "procedencia, confianza, creado_en, actualizado_en) VALUES("
            "'fact-legacy', 'inst', 'dato', 'a legacy fact', 0, '[]', '', 1.0, "
            "'2026-01-01', '2026-01-01')"
        )
        await conn.execute(
            "INSERT INTO knowledge(id, instance_id, source_path, chunk_index, "
            "text, embedding, mtime, size, ingested_at) VALUES("
            "'kn-legacy', 'inst', 'notes.md', 0, 'hi', '[]', 0.0, -1, '2026-01-01')"
        )
        await conn.commit()

    # Now run the real init_db(), which walks migrations 11.. from version 10.
    await db.init_db()

    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT actor_id FROM approvals WHERE id = 'ap-legacy'"
        ) as cur:
            row = await cur.fetchone()
        assert row["actor_id"] == "carol"

        async with conn.execute(
            "SELECT actor_id FROM approvals WHERE id = 'ap-no-actor'"
        ) as cur:
            row = await cur.fetchone()
        assert row["actor_id"] == db.Config.root_admin_id

        async with conn.execute(
            "SELECT actor_id FROM facts WHERE id = 'fact-legacy'"
        ) as cur:
            row = await cur.fetchone()
        assert row["actor_id"] == db.Config.root_admin_id

        async with conn.execute(
            "SELECT actor_id FROM knowledge WHERE id = 'kn-legacy'"
        ) as cur:
            row = await cur.fetchone()
        assert row["actor_id"] == db.Config.root_admin_id

        # The pre-existing rows are still exactly there; additive, not lossy.
        async with conn.execute("SELECT COUNT(*) FROM approvals") as cur:
            assert (await cur.fetchone())[0] == 2
