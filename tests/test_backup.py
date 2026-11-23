"""
Tests for lhassa.memory.backup; incremental backup + restore-and-verify (§2.10).

Offline; the grande and the backup dir are rerouted to tmp paths via monkeypatch.
"""

import pytest


async def _init(tmp_path, monkeypatch):
    """Reroute the grande (db) and the backup dir to tmp; init + seed the grande."""
    import lhassa.core.db as db
    import lhassa.memory.backup as backup
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    await db.init_db()
    return db, backup


# ── backup + verify ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backup_creates_file_and_verifies(tmp_path, monkeypatch):
    db, backup = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_personal", "dato", "un dato", pinned=True)

    path = await backup.backup()
    from pathlib import Path
    assert Path(path).exists()
    assert Path(path).stat().st_size > 0
    assert await backup.verify(path) is True


@pytest.mark.asyncio
async def test_backup_to_explicit_dest(tmp_path, monkeypatch):
    db, backup = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_personal", "dato", "x")

    dest = tmp_path / "mirror" / "grande.bak"
    path = await backup.backup(dest=dest)
    assert path == str(dest)
    assert dest.exists()
    assert await backup.verify(dest) is True


@pytest.mark.asyncio
async def test_backup_is_rerunnable_incremental(tmp_path, monkeypatch):
    db, backup = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_personal", "dato", "first")
    p1 = await backup.backup()
    await facts.add_fact("inst_personal", "dato", "second")
    p2 = await backup.backup()  # cheap re-run, overwrites the same mirror
    assert p1 == p2
    assert await backup.verify(p2) is True


# ── verify rejects bad files ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_false_on_empty_file(tmp_path, monkeypatch):
    _, backup = await _init(tmp_path, monkeypatch)
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    assert await backup.verify(empty) is False


@pytest.mark.asyncio
async def test_verify_false_on_corrupt_file(tmp_path, monkeypatch):
    _, backup = await _init(tmp_path, monkeypatch)
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is not a sqlite database at all, just garbage bytes")
    assert await backup.verify(corrupt) is False


@pytest.mark.asyncio
async def test_verify_false_on_missing_file(tmp_path, monkeypatch):
    _, backup = await _init(tmp_path, monkeypatch)
    assert await backup.verify(tmp_path / "nope.db") is False


@pytest.mark.asyncio
async def test_verify_false_when_tables_missing(tmp_path, monkeypatch):
    _, backup = await _init(tmp_path, monkeypatch)
    # A valid SQLite file but without the grande's tables.
    from lhassa._vendor import aiosqlite
    bare = tmp_path / "bare.db"
    async with aiosqlite.connect(bare) as conn:
        await conn.execute("CREATE TABLE unrelated (x INTEGER)")
        await conn.commit()
    assert await backup.verify(bare) is False


@pytest.mark.asyncio
async def test_verify_false_on_malformed_current_schema(tmp_path, monkeypatch):
    db, backup = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite

    malformed = tmp_path / "malformed-current.db"
    await backup.backup(dest=malformed)
    async with aiosqlite.connect(malformed) as conn:
        await conn.execute("DROP TABLE knowledge")
        await conn.commit()

    assert await backup.verify(malformed) is False


@pytest.mark.asyncio
async def test_verify_false_on_malformed_legacy_schema(tmp_path, monkeypatch):
    _, backup = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite

    malformed = tmp_path / "malformed-legacy.db"
    async with aiosqlite.connect(malformed) as conn:
        for table in ("episodes", "facts", "tasks"):
            await conn.execute(f"CREATE TABLE {table}(id TEXT PRIMARY KEY)")
        await conn.commit()

    assert await backup.verify(malformed) is False


@pytest.mark.asyncio
async def test_verify_false_when_file_disappears_before_stat(tmp_path, monkeypatch):
    _, backup = await _init(tmp_path, monkeypatch)
    from pathlib import Path

    victim = tmp_path / "racing.db"
    victim.write_bytes(b"sqlite-ish")
    original_stat = Path.stat

    def racing_stat(self, *args, **kwargs):
        if self == victim:
            raise FileNotFoundError(victim)
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", racing_stat)
    assert await backup.verify(victim) is False


# ── restore round-trips ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restore_round_trip(tmp_path, monkeypatch):
    db, backup = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_personal", "dato", "valuable knowledge", pinned=True)

    path = await backup.backup()

    # Wipe the grande, then confirm it's really gone.
    db_path = tmp_path / "test_brain.db"
    db_path.unlink()
    await db.init_db()  # fresh empty grande
    assert await facts.load_facts("inst_personal") == []

    # Restore and confirm the data is back.
    await backup.restore(path)
    restored = await facts.load_facts("inst_personal")
    assert any(f.contenido == "valuable knowledge" for f in restored)

    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] == db.SCHEMA_VERSION


@pytest.mark.asyncio
async def test_restore_to_explicit_dest(tmp_path, monkeypatch):
    db, backup = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_personal", "dato", "payload")
    path = await backup.backup()

    other = tmp_path / "restored.db"
    await backup.restore(path, dest=other)
    assert await backup.verify(other) is True


@pytest.mark.asyncio
async def test_legacy_unversioned_backup_can_restore_then_upgrade(tmp_path, monkeypatch):
    db, backup = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite

    legacy = tmp_path / "legacy.db"
    await backup.backup(dest=legacy)
    async with aiosqlite.connect(legacy) as conn:
        await conn.execute("PRAGMA user_version = 0")
        await conn.commit()

    assert await backup.verify(legacy) is True
    restored = tmp_path / "restored-legacy.db"
    await backup.restore(legacy, dest=restored)
    monkeypatch.setattr(db, "DB_PATH", restored)
    await db.init_db()
    async with aiosqlite.connect(restored) as conn:
        async with conn.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] == db.SCHEMA_VERSION


@pytest.mark.asyncio
async def test_restore_rejects_too_new_backup_without_touching_dest(
    tmp_path, monkeypatch
):
    db, backup = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite

    future = tmp_path / "future.db"
    await backup.backup(dest=future)
    async with aiosqlite.connect(future) as conn:
        await conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        await conn.commit()
    assert await backup.verify(future) is False

    dest = tmp_path / "existing.db"
    async with aiosqlite.connect(dest) as conn:
        await conn.execute("CREATE TABLE sentinel(value TEXT)")
        await conn.execute("INSERT INTO sentinel VALUES('keep')")
        await conn.commit()

    with pytest.raises(ValueError, match="unsupported schema"):
        await backup.restore(future, dest=dest)
    async with aiosqlite.connect(dest) as conn:
        async with conn.execute("SELECT value FROM sentinel") as cur:
            assert await cur.fetchone() == ("keep",)


@pytest.mark.asyncio
async def test_restore_rejects_malformed_current_without_touching_dest(
    tmp_path, monkeypatch
):
    db, backup = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite

    malformed = tmp_path / "malformed-current.db"
    await backup.backup(dest=malformed)
    async with aiosqlite.connect(malformed) as conn:
        await conn.execute("DROP TABLE knowledge")
        await conn.commit()

    dest = tmp_path / "existing-malformed-dest.db"
    async with aiosqlite.connect(dest) as conn:
        await conn.execute("CREATE TABLE sentinel(value TEXT)")
        await conn.execute("INSERT INTO sentinel VALUES('keep')")
        await conn.commit()

    with pytest.raises(ValueError, match="corrupt or has an unsupported schema"):
        await backup.restore(malformed, dest=dest)
    async with aiosqlite.connect(dest) as conn:
        async with conn.execute("SELECT value FROM sentinel") as cur:
            assert await cur.fetchone() == ("keep",)


# ── recover something from days ago by tag (DoD) ─────────────────────────────────

@pytest.mark.asyncio
async def test_recover_old_fact_by_tag_survives_backup_restore(tmp_path, monkeypatch):
    db, backup = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load

    # An "old" fact carrying a tag, written to the grande days ago.
    await facts.add_fact(
        "inst_personal", "dato", "examen de cálculo el 15",
        tags=["agenda"], procedencia="owner",
    )
    # Some unrelated noise.
    await facts.add_fact("inst_personal", "dato", "ruido", tags=["otro"])

    # Retrieval by tag straight from the grande (explainable, exact membership).
    by_tag = await facts.load_by_tags("inst_personal", ["agenda"])
    assert any(f.contenido == "examen de cálculo el 15" for f in by_tag)

    # And through the layered load() with active_tags.
    ctx = await load.load("inst_personal", token_budget=10_000, active_tags=["agenda"])
    assert any(f.contenido == "examen de cálculo el 15" for f in ctx.tag_facts)

    # Survives a backup + restore cycle.
    path = await backup.backup()
    (tmp_path / "test_brain.db").unlink()
    await db.init_db()
    await backup.restore(path)
    again = await facts.load_by_tags("inst_personal", ["agenda"])
    assert any(f.contenido == "examen de cálculo el 15" for f in again)
