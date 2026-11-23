"""Tests for lhassa.memory.store. SQLite persistence layer."""

import pytest
import pytest_asyncio  # noqa: F401


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _init(tmp_db, monkeypatch):
    import lhassa.memory.store as store
    monkeypatch.setattr(store, "DB_PATH", tmp_db)
    await store.init_db()
    return store


# ── init_db ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_db, monkeypatch):
    await _init(tmp_db, monkeypatch)
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(tmp_db) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
    assert {"facts", "sessions", "scheduled_tasks"}.issubset(tables)


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    # Calling a second time must not raise
    await store.init_db()


# ── upsert_fact / load_facts ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_fact_insert(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    await store.upsert_fact("name", "Alice")
    facts = await store.load_facts()
    assert len(facts) == 1
    assert facts[0]["key"] == "name"
    assert facts[0]["value"] == "Alice"


@pytest.mark.asyncio
async def test_upsert_fact_update(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    await store.upsert_fact("name", "Alice")
    await store.upsert_fact("name", "Bob")
    facts = await store.load_facts()
    assert len(facts) == 1
    assert facts[0]["value"] == "Bob"


@pytest.mark.asyncio
async def test_load_facts_empty(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    assert await store.load_facts() == []


@pytest.mark.asyncio
async def test_load_facts_multiple(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    await store.upsert_fact("name", "Alice")
    await store.upsert_fact("city", "Madrid")
    facts = await store.load_facts()
    assert len(facts) == 2
    keys = {f["key"] for f in facts}
    assert keys == {"name", "city"}


# ── save_session / load_recent_sessions ────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_session(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    await store.save_session("User asked about the weather.")
    sessions = await store.load_recent_sessions()
    assert len(sessions) == 1
    assert sessions[0]["summary"] == "User asked about the weather."


@pytest.mark.asyncio
async def test_load_recent_sessions_limit(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    for i in range(10):
        await store.save_session(f"Session {i}")
    sessions = await store.load_recent_sessions(n=3)
    assert len(sessions) == 3
    # Most recent first
    assert sessions[0]["summary"] == "Session 9"


@pytest.mark.asyncio
async def test_save_session_trims_to_100(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    for i in range(105):
        await store.save_session(f"Session {i}")
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(tmp_db) as db:
        async with db.execute("SELECT COUNT(*) FROM sessions") as cur:
            (count,) = await cur.fetchone()
    assert count == 100


@pytest.mark.asyncio
async def test_save_session_trim_keeps_newest(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    for i in range(105):
        await store.save_session(f"Session {i}")
    sessions = await store.load_recent_sessions(n=100)
    summaries = [s["summary"] for s in sessions]
    # The oldest 5 should have been pruned
    assert "Session 0" not in summaries
    assert "Session 104" in summaries


# ── add_scheduled_task / load_pending_tasks / mark_task_done ──────────────────

@pytest.mark.asyncio
async def test_add_scheduled_task_returns_id(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    task_id = await store.add_scheduled_task(
        "morning alarm", "2025-01-01T08:00:00+00:00", "Good morning!"
    )
    assert isinstance(task_id, int)
    assert task_id > 0


@pytest.mark.asyncio
async def test_load_pending_tasks_past(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    await store.add_scheduled_task(
        "past reminder", "2000-01-01T00:00:00+00:00", "Old message"
    )
    tasks = await store.load_pending_tasks()
    assert len(tasks) == 1
    assert tasks[0]["name"] == "past reminder"


@pytest.mark.asyncio
async def test_load_pending_tasks_future(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    await store.add_scheduled_task(
        "future reminder", "2099-01-01T00:00:00+00:00", "Future message"
    )
    tasks = await store.load_pending_tasks()
    assert tasks == []


@pytest.mark.asyncio
async def test_mark_task_done(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    task_id = await store.add_scheduled_task(
        "alarm", "2000-01-01T00:00:00+00:00", "Ring!"
    )
    await store.mark_task_done(task_id)
    tasks = await store.load_pending_tasks()
    assert tasks == []


@pytest.mark.asyncio
async def test_load_all_scheduled_tasks(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    await store.add_scheduled_task("t1", "2000-01-01T00:00:00+00:00", "m1")
    await store.add_scheduled_task("t2", "2099-01-01T00:00:00+00:00", "m2")
    tasks = await store.load_all_scheduled_tasks()
    assert len(tasks) == 2


# ── build_memory_context ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_memory_context_empty(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    result = await store.build_memory_context()
    assert result is None


@pytest.mark.asyncio
async def test_build_memory_context_with_facts(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    await store.upsert_fact("name", "Alice")
    result = await store.build_memory_context()
    assert result is not None
    assert "name" in result
    assert "Alice" in result


@pytest.mark.asyncio
async def test_build_memory_context_with_sessions(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    await store.save_session("User asked about the weather.")
    result = await store.build_memory_context()
    assert result is not None
    assert "weather" in result


@pytest.mark.asyncio
async def test_build_memory_context_sections(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)
    await store.upsert_fact("city", "Madrid")
    await store.save_session("Discussed travel plans.")
    result = await store.build_memory_context()
    assert "What you know about the user" in result
    assert "Recent conversations" in result


@pytest.mark.asyncio
async def test_replace_memory_snapshot_replaces_all_tables(tmp_db, monkeypatch):
    store = await _init(tmp_db, monkeypatch)

    await store.replace_memory_snapshot(
        facts=[{"key": "name", "value": "Alice", "updated": "2026-03-31T00:00:00+00:00"}],
        sessions=[{"date": "2026-03-31T00:00:00+00:00", "summary": "Imported session"}],
        scheduled_tasks=[{
            "name": "Reminder",
            "run_at": "2099-01-01T00:00:00+00:00",
            "message": "Ping",
            "status": "pending",
            "created_at": "2026-03-31T00:00:00+00:00",
        }],
    )

    facts = await store.load_facts()
    sessions = await store.load_recent_sessions(10)
    tasks = await store.load_all_scheduled_tasks()

    assert facts == [{"key": "name", "value": "Alice", "updated": "2026-03-31T00:00:00+00:00"}]
    assert sessions == [{"date": "2026-03-31T00:00:00+00:00", "summary": "Imported session"}]
    assert len(tasks) == 1
    assert tasks[0]["name"] == "Reminder"
