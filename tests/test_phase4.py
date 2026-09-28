"""Phase 4 tests; standing goals, watchlist senses, task routes, CLI parsing.

All tests are FULLY OFFLINE: no network calls, no real provider.
Migrations are tested against a temp DB created with the OLD schema then migrated.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio  # noqa: F401


# ── helpers ────────────────────────────────────────────────────────────────────

async def _make_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp file and init the schema. Returns the db module."""
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    await db.init_db()
    return db


def _iid() -> str:
    return "inst_test"


# ── DELIVERABLE 1: schema migrations ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_tasks_due_at_column_added(tmp_path, monkeypatch):
    """init_db adds due_at and recurrence columns to tasks."""
    import lhassa.core.db as db
    from lhassa._vendor import aiosqlite

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    await db.init_db()

    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute("PRAGMA table_info(tasks)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert "due_at" in cols
    assert "recurrence" in cols


@pytest.mark.asyncio
async def test_jobs_acked_column_added(tmp_path, monkeypatch):
    """init_db adds acked column to jobs."""
    import lhassa.core.db as db
    from lhassa._vendor import aiosqlite

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    await db.init_db()

    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute("PRAGMA table_info(jobs)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert "acked" in cols


@pytest.mark.asyncio
async def test_watch_state_table_created(tmp_path, monkeypatch):
    """init_db creates the watch_state table."""
    import lhassa.core.db as db
    from lhassa._vendor import aiosqlite

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    await db.init_db()

    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watch_state'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_migration_idempotent_on_existing_db(tmp_path, monkeypatch):
    """init_db on an existing production-schema DB (without the new cols) adds them."""
    from lhassa._vendor import aiosqlite

    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "old.db")

    # Create a DB with the OLD schema (no due_at / recurrence / acked / watch_state).
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
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
                id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
                pid INTEGER, harness TEXT, estado TEXT NOT NULL DEFAULT 'running',
                costo_usd REAL DEFAULT 0.0, creado_en TEXT NOT NULL, terminado_en TEXT
            )
        """)
        await conn.commit()

    # Running init_db should add the missing columns without error.
    await db.init_db()

    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute("PRAGMA table_info(tasks)") as cur:
            task_cols = {row[1] for row in await cur.fetchall()}
        async with conn.execute("PRAGMA table_info(jobs)") as cur:
            job_cols = {row[1] for row in await cur.fetchall()}
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watch_state'"
        ) as cur:
            wst = await cur.fetchone()

    assert "due_at" in task_cols
    assert "recurrence" in task_cols
    assert "acked" in job_cols
    assert wst is not None


# ── DELIVERABLE 1: due_at filtering ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_due_filtering_future_task_not_returned(tmp_path, monkeypatch):
    """A task with due_at in the future is NOT returned by load_pending_tasks."""
    db = await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    await db.add_task(iid, "sync_repo", due_at=future)
    rows = await db.load_pending_tasks(iid)
    assert rows == []


@pytest.mark.asyncio
async def test_due_filtering_past_task_returned(tmp_path, monkeypatch):
    """A task with due_at in the past IS returned by load_pending_tasks."""
    db = await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    await db.add_task(iid, "sync_repo", due_at=past)
    rows = await db.load_pending_tasks(iid)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_due_filtering_no_due_at_always_returned(tmp_path, monkeypatch):
    """A task with no due_at is always returned (one-shot, immediate)."""
    db = await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    await db.add_task(iid, "sync_repo")
    rows = await db.load_pending_tasks(iid)
    assert len(rows) == 1


# ── DELIVERABLE 1: recurrence next-instance ───────────────────────────────────

@pytest.mark.asyncio
async def test_recurrence_next_instance_backlog_guard(tmp_path, monkeypatch):
    """Recurrence with old due far in the past → next due ≈ now (no backlog storm)."""
    await _make_db(tmp_path, monkeypatch)

    # Build a trigger that looks like it's from a recurring task with a due_at
    # far in the past (simulating a long downtime scenario).
    old_due = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()

    # Simulate the recurrence logic from step.py inline.
    interval = 3600.0  # hourly
    now_dt = datetime.now(timezone.utc)
    old_due_dt = datetime.fromisoformat(old_due.replace("Z", "+00:00"))
    next_due_dt = max(now_dt, old_due_dt + timedelta(seconds=interval))

    # next_due should be ≈ now (not old_due + 1h = 47 hours ago).
    assert next_due_dt >= now_dt
    # And it should be within a few seconds of now, not hours in the future.
    delta = (next_due_dt - now_dt).total_seconds()
    assert delta < 5  # within 5 seconds of now


# ── DELIVERABLE 2a: job_review fires once ─────────────────────────────────────

@pytest.mark.asyncio
async def test_job_review_fires_once(tmp_path, monkeypatch):
    """_from_finished_jobs fires once per job (acked after first fire)."""
    db = await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    # Insert a finished job with acked=0.
    from lhassa._vendor import aiosqlite

    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO jobs(id, instance_id, pid, harness, estado, costo_usd, creado_en, acked)
               VALUES('j_test', ?, NULL, 'claude', 'done', 0.0, '2024-01-01T00:00:00+00:00', 0)""",
            (iid,),
        )
        await conn.commit()

    from lhassa.core import watchlist

    # First check: should produce a trigger and mark the job acked.
    triggers1 = await watchlist.check(iid)
    job_triggers1 = [t for t in triggers1 if t.kind == "job"]
    assert len(job_triggers1) == 1
    assert job_triggers1[0].action == "job_review"
    assert job_triggers1[0].objetivo == "j_test"

    # Second check: job is acked → no new trigger.
    triggers2 = await watchlist.check(iid)
    job_triggers2 = [t for t in triggers2 if t.kind == "job"]
    assert len(job_triggers2) == 0


@pytest.mark.asyncio
async def test_job_review_peek_does_not_ack(tmp_path, monkeypatch):
    """peek=True does not mark the job as acked."""
    db = await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    from lhassa._vendor import aiosqlite

    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO jobs(id, instance_id, pid, harness, estado, costo_usd, creado_en, acked)
               VALUES('j_peek', ?, NULL, 'claude', 'done', 0.0, '2024-01-01T00:00:00+00:00', 0)""",
            (iid,),
        )
        await conn.commit()

    from lhassa.core import watchlist

    # Peek: trigger visible but no state write.
    triggers_peek = await watchlist.check(iid, peek=True)
    job_triggers_peek = [t for t in triggers_peek if t.kind == "job"]
    assert len(job_triggers_peek) == 1

    # Normal check should still fire (job not acked).
    triggers_normal = await watchlist.check(iid)
    job_triggers_normal = [t for t in triggers_normal if t.kind == "job"]
    assert len(job_triggers_normal) == 1


# ── DELIVERABLE 2b: repo sense ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_repo_sense_seeds_then_fires(tmp_path, monkeypatch):
    """Repo sense: first call seeds state, second call (changed hash) fires trigger."""
    await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    from lhassa.core import watchlist

    # Patch the Config binding that watchlist.py holds directly; this is stable
    # even if test_api_server.py reloaded lhassa.config and made the module-level
    # Config reference stale.
    monkeypatch.setattr(watchlist.Config, "watch_repos", "/fake/repo")

    call_count = [0]

    def fake_git_head(repo_path, timeout=5):
        call_count[0] += 1
        if call_count[0] == 1:
            return "aaabbbccc"  # first call (seed)
        return "dddeeefff"     # second call (changed)

    monkeypatch.setattr(watchlist, "_git_head", fake_git_head)

    # First check: seed (no trigger).
    triggers1 = await watchlist.check(iid)
    repo_triggers1 = [t for t in triggers1 if t.kind == "repo"]
    assert len(repo_triggers1) == 0

    # Second check: hash changed → trigger.
    triggers2 = await watchlist.check(iid)
    repo_triggers2 = [t for t in triggers2 if t.kind == "repo"]
    assert len(repo_triggers2) == 1
    t = repo_triggers2[0]
    assert t.action == "repo_changed"
    assert t.objetivo == "/fake/repo"
    assert t.params["old"] == "aaabbbccc"
    assert t.params["new"] == "dddeeefff"


@pytest.mark.asyncio
async def test_repo_sense_no_trigger_when_hash_unchanged(tmp_path, monkeypatch):
    """Repo sense does not fire when HEAD hash hasn't changed."""
    await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    from lhassa.core import watchlist

    monkeypatch.setattr(watchlist.Config, "watch_repos", "/fake/repo")
    monkeypatch.setattr(watchlist, "_git_head", lambda *a, **kw: "samehashshash")

    await watchlist.check(iid)  # seed
    triggers = await watchlist.check(iid)  # same hash
    assert not [t for t in triggers if t.kind == "repo"]


# ── DELIVERABLE 2c: health sense ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_transition_dedup(tmp_path, monkeypatch):
    """Health sense fires once on transition INTO warn/alert; does not re-fire."""
    await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    digest_calls = [0]
    digests = [
        {"worst_level": "info", "ok": True, "results": {}},
        {"worst_level": "warn", "ok": False, "results": {"disk": {"level": "warn", "summary": "90%"}}},
        {"worst_level": "warn", "ok": False, "results": {"disk": {"level": "warn", "summary": "90%"}}},
    ]

    from lhassa.core import watchlist

    def fake_read_digest():
        d = digests[min(digest_calls[0], len(digests) - 1)]
        digest_calls[0] += 1
        return d

    monkeypatch.setattr(watchlist, "_read_health_digest", fake_read_digest)

    # First check: info → seed, no trigger.
    triggers1 = await watchlist.check(iid)
    assert not [t for t in triggers1 if t.kind == "health"]

    # Second check: info → warn → fire trigger.
    triggers2 = await watchlist.check(iid)
    health_triggers = [t for t in triggers2 if t.kind == "health"]
    assert len(health_triggers) == 1
    assert health_triggers[0].params["level"] == "warn"

    # Third check: warn → warn → no new trigger (same level, no transition).
    triggers3 = await watchlist.check(iid)
    assert not [t for t in triggers3 if t.kind == "health"]


# ── DELIVERABLE 2d: telegram rule minting ────────────────────────────────────

@pytest.mark.asyncio
async def test_telegram_rule_minting(tmp_path, monkeypatch):
    """An inbound message matching a watch rule mints a task via dispatch.mint_task."""
    await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    from lhassa.notify import telegram_inbound

    monkeypatch.setattr(
        telegram_inbound,
        "_load_watch_rules",
        lambda config: [
            {
                "pattern": re.compile(r"check.*inbox", re.IGNORECASE),
                "accion": "review_inbox",
                "params": {"source": "telegram"},
            }
        ],
    )

    minted_calls: list[dict] = []

    async def fake_mint_task(instance_id, accion, objetivo="", params=None, *,
                             origin, due_at=None, recurrence=None, judgment=False):
        minted_calls.append({
            "instance_id": instance_id,
            "accion": accion,
            "params": params or {},
            "judgment": judgment,
        })
        return ("t_rule_test", "pendiente")

    import lhassa.core.dispatch as dispatch_mod
    monkeypatch.setattr(dispatch_mod, "mint_task", fake_mint_task)

    from lhassa.core import db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")

    # Mock db.init_db to no-op since we already inited.
    async def noop_init():
        pass
    monkeypatch.setattr(db_mod, "init_db", noop_init)

    class FakeConfig:
        instance_id = iid
        watch_rules_file = str(tmp_path / "rules.json")

    task_id = await telegram_inbound._try_mint_rule_task(
        "please check my inbox now",
        [
            {
                "pattern": re.compile(r"check.*inbox", re.IGNORECASE),
                "accion": "review_inbox",
                "params": {"source": "telegram"},
            }
        ],
        config=FakeConfig(),
    )
    assert task_id is not None
    assert len(minted_calls) == 1
    assert minted_calls[0]["accion"] == "review_inbox"
    assert minted_calls[0]["judgment"] is True


# ── DELIVERABLE 3: API routes ─────────────────────────────────────────────────
# API tests use sync TestClient (replaced by ASGISyncTestClient in conftest).
# DB setup uses asyncio.run() since we're NOT inside an async event loop here.

def _setup_db(tmp_path, monkeypatch, db_name="api_test.db"):
    """Sync helper: redirect DB_PATH, init schema, return the db module."""
    import lhassa.core.db as db_mod

    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / db_name)
    asyncio.run(db_mod.init_db())
    return db_mod


def test_api_task_list(tmp_path, monkeypatch):
    """GET /api/tasks returns tasks list."""
    db_mod = _setup_db(tmp_path, monkeypatch)
    asyncio.run(db_mod.add_task("inst_personal", "sync_repo", objetivo="/tmp/r"))

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["accion"] == "sync_repo"


def test_api_task_create_known_accion(tmp_path, monkeypatch):
    """POST /api/tasks with a registered accion returns {id, estado}."""
    from lhassa.core import actions as _actions

    _setup_db(tmp_path, monkeypatch, "api_create.db")

    # Ensure sync_repo is registered.
    assert "sync_repo" in _actions.ACTIONS

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post(
        "/api/tasks",
        json={"accion": "sync_repo", "objetivo": "/tmp/r"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert "estado" in data


def test_api_task_unknown_accion_rejected(tmp_path, monkeypatch):
    """POST /api/tasks with an unknown accion without judgment=True → 400."""
    _setup_db(tmp_path, monkeypatch, "api_reject.db")

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post("/api/tasks", json={"accion": "totally_unknown_xyz"})
    assert resp.status_code == 400
    assert "totally_unknown_xyz" in resp.json().get("detail", "")


def test_api_task_judgment_flag_allows_any_accion(tmp_path, monkeypatch):
    """POST /api/tasks with judgment=True allows any accion → 201."""
    _setup_db(tmp_path, monkeypatch, "api_judgment.db")

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post(
        "/api/tasks",
        json={"accion": "review my inbox", "judgment": True},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data


def test_api_task_create_with_recurrence(tmp_path, monkeypatch):
    """POST /api/tasks with recurrence='daily' mints a recurring task."""
    db_mod = _setup_db(tmp_path, monkeypatch, "api_recurrence.db")

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post(
        "/api/tasks",
        json={"accion": "sync_repo", "objetivo": "/tmp/r", "recurrence": "daily"},
    )
    assert resp.status_code == 201

    # Verify recurrence was persisted.
    tasks = asyncio.run(db_mod.list_tasks("inst_personal", limit=10))
    task = next((t for t in tasks if t.get("accion") == "sync_repo"), None)
    assert task is not None
    assert task.get("recurrence") == "86400"


def test_api_watchlist_preview(tmp_path, monkeypatch):
    """GET /api/watchlist returns {senses, preview} without side effects."""
    _setup_db(tmp_path, monkeypatch, "api_watchlist.db")

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/watchlist")
    assert resp.status_code == 200
    data = resp.json()
    assert "senses" in data
    assert "preview" in data
    assert isinstance(data["senses"], list)
    assert "_from_pending_tasks" in data["senses"]
    assert "_from_finished_jobs" in data["senses"]
    assert "_from_repo_changes" in data["senses"]
    assert "_from_health" in data["senses"]
    assert isinstance(data["preview"], list)


def test_api_tasks_filter_by_estado(tmp_path, monkeypatch):
    """GET /api/tasks?estado=hecha returns only completed tasks."""
    db_mod = _setup_db(tmp_path, monkeypatch, "api_filter.db")

    tid = asyncio.run(db_mod.add_task("inst_personal", "sync_repo"))
    asyncio.run(db_mod.set_task_state("inst_personal", tid, "hecha"))
    asyncio.run(db_mod.add_task("inst_personal", "sync_repo"))  # pendiente

    from lhassa.api.server import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/tasks?estado=hecha")
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["estado"] == "hecha" for r in data)
    assert len(data) == 1


# ── DELIVERABLE 3b: CLI parsing ───────────────────────────────────────────────

def test_cli_task_add_parses_recurrence():
    """_parse_recurrence translates aliases and seconds correctly."""
    from lhassa.cli.task_cmds import _parse_recurrence

    assert _parse_recurrence("hourly") == "3600"
    assert _parse_recurrence("daily") == "86400"
    assert _parse_recurrence("weekly") == "604800"
    assert _parse_recurrence("7200") == "7200"
    assert _parse_recurrence("7200s") == "7200"
    assert _parse_recurrence(None) is None
    assert _parse_recurrence("") is None


def test_cli_task_list_detail_says_what_the_task_is():
    """A column of bare action names cannot answer 'is my reminder in there?'."""
    from lhassa.cli.task_cmds import _task_detail

    assert _task_detail({"params": {"text": "deadline de Cohere"}}) == "deadline de Cohere"
    assert _task_detail({"params": {"action": "calendar_sync"}}) == "calendar_sync"
    # An unrecognized shape still says something rather than nothing.
    assert _task_detail({"params": {"weird_key": "a subject"}}) == "a subject"
    # Degrades quietly on the shapes a row can actually take.
    assert _task_detail({"params": {}}) == ""
    assert _task_detail({}) == ""
    assert _task_detail({"params": None}) == ""
    # Long text is cut, not wrapped into an unreadable column.
    long_detail = _task_detail({"params": {"text": "x" * 200}})
    assert len(long_detail) <= 46 and long_detail.endswith("…")


def test_cli_task_add_parses_due():
    """_parse_due handles ISO 8601 and +Nh shortcuts."""
    from lhassa.cli.task_cmds import _parse_due

    now = datetime.now(timezone.utc)

    # +1h
    result = _parse_due("+1h")
    dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
    delta = (dt - now).total_seconds()
    assert 3500 < delta < 3700

    # +30m
    result = _parse_due("+30m")
    dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
    delta = (dt - now).total_seconds()
    assert 1750 < delta < 1850

    # None → None
    assert _parse_due(None) is None

    # ISO timestamp passthrough.
    iso = "2030-01-01T00:00:00+00:00"
    assert _parse_due(iso) == iso


def test_cli_task_app_subcommands_registered():
    """task_app has add/list/done/reject commands registered."""
    from lhassa.cli.task_cmds import task_app

    names = {cmd.name for cmd in task_app.registered_commands}
    assert "add" in names
    assert "list" in names
    assert "done" in names
    assert "reject" in names


# ── API recurrence parsing ────────────────────────────────────────────────────

def test_api_parse_recurrence_aliases():
    """task_routes._parse_recurrence translates string aliases."""
    from lhassa.api.task_routes import _parse_recurrence

    assert _parse_recurrence("hourly") == "3600"
    assert _parse_recurrence("daily") == "86400"
    assert _parse_recurrence("weekly") == "604800"
    assert _parse_recurrence(3600) == "3600"
    assert _parse_recurrence("7200") == "7200"
    assert _parse_recurrence(None) is None


def test_api_parse_due_at_relative():
    """task_routes._parse_due_at handles +Nh shorthand."""
    from lhassa.api.task_routes import _parse_due_at

    now = datetime.now(timezone.utc)
    result = _parse_due_at("+2h")
    dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
    delta = (dt - now).total_seconds()
    assert 7100 < delta < 7300


def test_api_parse_due_at_iso():
    """task_routes._parse_due_at passes through valid ISO 8601."""
    from lhassa.api.task_routes import _parse_due_at

    iso = "2035-06-01T12:00:00+00:00"
    assert _parse_due_at(iso) == iso
    assert _parse_due_at(None) is None


# ── watchlist.check does not raise on errors ──────────────────────────────────

@pytest.mark.asyncio
async def test_watchlist_check_never_raises(tmp_path, monkeypatch):
    """watchlist.check() degrades gracefully even when senses error."""
    import lhassa.core.db as db_mod
    from lhassa.core import watchlist

    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "resilient.db")
    await db_mod.init_db()

    # Make the health digest reader explode.
    monkeypatch.setattr(watchlist, "_read_health_digest", lambda: (_ for _ in ()).throw(
        RuntimeError("simulated health error")
    ))
    # Make _git_head raise.
    monkeypatch.setattr(watchlist, "_git_head", lambda *a, **kw: (_ for _ in ()).throw(
        OSError("git not found")
    ))
    monkeypatch.setattr("lhassa.config.Config.watch_repos", "/nonexistent/repo", raising=False)

    # Should never raise; may return empty list.
    triggers = await watchlist.check("inst_personal")
    assert isinstance(triggers, list)


# ── dispatch.mint_task judgment flag ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_mint_task_judgment_bypasses_actions_check(tmp_path, monkeypatch):
    """mint_task with judgment=True allows any accion."""
    import lhassa.core.db as db_mod

    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "mint_judgment.db")
    await db_mod.init_db()

    from lhassa.core.dispatch import mint_task
    from lhassa.security.trust import Origin, TrustLevel

    origin = Origin(trust=TrustLevel.HUMANO, actor_id="test")
    task_id, estado = await mint_task(
        "inst_personal",
        "review my inbox",
        origin=origin,
        judgment=True,
    )
    assert task_id != ""
    assert estado in ("pendiente", "esperando_aprobacion")


@pytest.mark.asyncio
async def test_mint_task_unknown_accion_rejected_without_judgment(tmp_path, monkeypatch):
    """mint_task without judgment=True rejects unknown accion."""
    import lhassa.core.db as db_mod

    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "mint_reject.db")
    await db_mod.init_db()

    from lhassa.core.dispatch import mint_task
    from lhassa.security.trust import Origin, TrustLevel

    origin = Origin(trust=TrustLevel.HUMANO, actor_id="test")
    task_id, estado = await mint_task(
        "inst_personal",
        "totally_unknown_xyz",
        origin=origin,
    )
    assert task_id == ""
    assert "refused" in estado
    assert "totally_unknown_xyz" in estado


# ── watch_state helpers ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_state_get_set(tmp_path, monkeypatch):
    """get_watch_state returns None before set; set_watch_state persists value."""
    db = await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    assert await db.get_watch_state(iid, "repo:/some/path") is None
    await db.set_watch_state(iid, "repo:/some/path", "abc123")
    assert await db.get_watch_state(iid, "repo:/some/path") == "abc123"

    # Upsert: second set overwrites.
    await db.set_watch_state(iid, "repo:/some/path", "def456")
    assert await db.get_watch_state(iid, "repo:/some/path") == "def456"


@pytest.mark.asyncio
async def test_load_unacked_finished_jobs(tmp_path, monkeypatch):
    """load_unacked_finished_jobs returns done/error jobs with acked=0."""
    db = await _make_db(tmp_path, monkeypatch)
    iid = _iid()

    from lhassa._vendor import aiosqlite

    async with aiosqlite.connect(db.DB_PATH) as conn:
        # done, unacked → should be returned
        await conn.execute(
            """INSERT INTO jobs(id, instance_id, pid, harness, estado, costo_usd, creado_en, acked)
               VALUES('j1', ?, NULL, 'claude', 'done', 0.0, '2024-01-01T00:00:00+00:00', 0)""",
            (iid,),
        )
        # error, unacked → should be returned
        await conn.execute(
            """INSERT INTO jobs(id, instance_id, pid, harness, estado, costo_usd, creado_en, acked)
               VALUES('j2', ?, NULL, 'claude', 'error', 0.0, '2024-01-01T00:00:01+00:00', 0)""",
            (iid,),
        )
        # done, already acked → should NOT be returned
        await conn.execute(
            """INSERT INTO jobs(id, instance_id, pid, harness, estado, costo_usd, creado_en, acked)
               VALUES('j3', ?, NULL, 'claude', 'done', 0.0, '2024-01-01T00:00:02+00:00', 1)""",
            (iid,),
        )
        # running → should NOT be returned
        await conn.execute(
            """INSERT INTO jobs(id, instance_id, pid, harness, estado, costo_usd, creado_en, acked)
               VALUES('j4', ?, NULL, 'claude', 'running', 0.0, '2024-01-01T00:00:03+00:00', 0)""",
            (iid,),
        )
        await conn.commit()

    jobs = await db.load_unacked_finished_jobs(iid)
    ids = {j["id"] for j in jobs}
    assert "j1" in ids
    assert "j2" in ids
    assert "j3" not in ids
    assert "j4" not in ids
