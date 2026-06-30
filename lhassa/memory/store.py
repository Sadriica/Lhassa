"""
Persistent SQLite store for Lhassa memory.

Tables:
  facts           — key/value facts about the user, extracted by the LLM
  sessions        — one-sentence summaries of past conversations
  scheduled_tasks — reminders set via the schedule_reminder tool
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lhassa._vendor import aiosqlite

DB_PATH = Path.home() / ".lhassa" / "memory.db"


async def init_db():
    """Create tables if they don't exist. Safe to call multiple times."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                key       TEXT NOT NULL,
                value     TEXT NOT NULL,
                updated   TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS facts_key ON facts(key)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                date      TEXT NOT NULL,
                summary   TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                run_at      TEXT NOT NULL,
                message     TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                created_at  TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS scheduled_tasks_pending
            ON scheduled_tasks(status, run_at)
        """)
        await db.commit()


# ── Facts ──────────────────────────────────────────────────────────────────────

async def upsert_fact(key: str, value: str):
    """Insert or update a fact. Uses ON CONFLICT for atomic upsert."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO facts(key, value, updated) VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated
        """, (key, value, now))
        await db.commit()


async def load_facts() -> list[dict]:
    """Return all stored facts, most recently updated first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT key, value, updated FROM facts ORDER BY updated DESC"
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


# ── Sessions ───────────────────────────────────────────────────────────────────

async def save_session(summary: str):
    """Persist a one-sentence session summary with a UTC timestamp.
    Keeps only the most recent 100 sessions to prevent unbounded growth."""
    date = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions(date, summary) VALUES(?, ?)", (date, summary)
        )
        # Trim to the 100 most recent rows
        await db.execute("""
            DELETE FROM sessions WHERE id NOT IN (
                SELECT id FROM sessions ORDER BY id DESC LIMIT 100
            )
        """)
        await db.commit()


async def load_recent_sessions(n: int = 5) -> list[dict]:
    """Return the N most recent session summaries."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT date, summary FROM sessions ORDER BY id DESC LIMIT ?", (n,)
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


# ── Scheduled tasks ────────────────────────────────────────────────────────────

async def add_scheduled_task(name: str, run_at: str, message: str) -> int:
    """Insert a new scheduled task. run_at must be ISO 8601 UTC."""
    created = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO scheduled_tasks(name, run_at, message, status, created_at)
               VALUES(?, ?, ?, 'pending', ?)""",
            (name, run_at, message, created),
        )
        await db.commit()
        return cur.lastrowid


async def load_pending_tasks() -> list[dict]:
    """Return pending tasks whose run_at is at or before the current UTC time."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM scheduled_tasks
               WHERE status='pending' AND run_at <= ?
               ORDER BY run_at""",
            (now,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def load_all_scheduled_tasks() -> list[dict]:
    """Return the 50 most recent scheduled tasks (any status)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scheduled_tasks ORDER BY run_at DESC LIMIT 50"
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def mark_task_done(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE scheduled_tasks SET status='done' WHERE id=?", (task_id,)
        )
        await db.commit()


async def replace_memory_snapshot(
    facts: list[dict] | None = None,
    sessions: list[dict] | None = None,
    scheduled_tasks: list[dict] | None = None,
):
    """Replace persisted memory tables with the provided snapshot."""
    await init_db()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM facts")
        await db.execute("DELETE FROM sessions")
        await db.execute("DELETE FROM scheduled_tasks")

        for fact in facts or []:
            key = str(fact.get("key", "")).strip()
            value = str(fact.get("value", "")).strip()
            updated = str(fact.get("updated", "")).strip() or datetime.now(timezone.utc).isoformat()
            if key and value:
                await db.execute(
                    "INSERT INTO facts(key, value, updated) VALUES(?, ?, ?)",
                    (key, value, updated),
                )

        for session in sessions or []:
            date = str(session.get("date", "")).strip() or datetime.now(timezone.utc).isoformat()
            summary = str(session.get("summary", "")).strip()
            if summary:
                await db.execute(
                    "INSERT INTO sessions(date, summary) VALUES(?, ?)",
                    (date, summary),
                )

        for task in scheduled_tasks or []:
            name = str(task.get("name", "")).strip()
            run_at = str(task.get("run_at", "")).strip()
            message = str(task.get("message", "")).strip()
            status = str(task.get("status", "pending")).strip() or "pending"
            created_at = str(task.get("created_at", "")).strip() or datetime.now(timezone.utc).isoformat()
            if name and run_at and message:
                await db.execute(
                    """
                    INSERT INTO scheduled_tasks(name, run_at, message, status, created_at)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (name, run_at, message, status, created_at),
                )

        await db.commit()


# ── Memory context for system prompt injection ─────────────────────────────────

async def build_memory_context() -> Optional[str]:
    """
    Build the memory block injected into the system prompt at startup.
    Returns None if the DB is empty (first session).
    Uses a single DB connection for both queries.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT key, value FROM facts ORDER BY updated DESC"
        ) as cur:
            facts = [dict(row) for row in await cur.fetchall()]
        async with db.execute(
            "SELECT date, summary FROM sessions ORDER BY id DESC LIMIT 5"
        ) as cur:
            sessions = [dict(row) for row in await cur.fetchall()]

    if not facts and not sessions:
        return None

    parts = []

    if facts:
        facts_text = "\n".join(f"- {f['key']}: {f['value']}" for f in facts)
        parts.append(f"## What you know about the user\n{facts_text}")

    if sessions:
        sessions_text = "\n".join(
            f"- [{s['date'][:10]}]: {s['summary']}" for s in reversed(sessions)
        )
        parts.append(f"## Recent conversations\n{sessions_text}")

    return "\n\n".join(parts)
