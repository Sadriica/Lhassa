"""Tests for lhassa.core.db; the append-only "grande" brain DB."""

import pytest
import pytest_asyncio  # noqa: F401


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


# ── init_db ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_init_db_creates_all_tables(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
    expected = {"episodes", "tasks", "jobs", "desired_state", "tools", "approvals"}
    assert expected.issubset(tables)


@pytest.mark.asyncio
async def test_init_db_enables_wal(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute("PRAGMA journal_mode") as cur:
            (mode,) = await cur.fetchone()
    assert mode.lower() == "wal"


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await db.init_db()  # second call must not raise


@pytest.mark.asyncio
async def test_every_table_has_instance_id(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        for table in ("episodes", "tasks", "jobs", "desired_state", "tools", "approvals"):
            async with conn.execute(f"PRAGMA table_info({table})") as cur:
                cols = {row[1] for row in await cur.fetchall()}
            assert "instance_id" in cols, f"{table} is missing instance_id"


# ── episodes (append-only) ──────────────────────────────────────────────────────

def _make_ep(instance_id, despertar_por="heartbeat"):
    from lhassa.core.episode import Episode
    return Episode.begin(despertar_por=despertar_por, instance_id=instance_id)


@pytest.mark.asyncio
async def test_append_and_load_recent_newest_first(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    ids = []
    for i in range(3):
        ep = _make_ep("inst_personal", despertar_por=f"trigger_{i}")
        ep.finish(decision=f"d{i}", accion=f"a{i}", resultado=f"r{i}")
        ids.append(await db.append_episode(ep))

    rows = await db.load_recent_episodes("inst_personal", n=20)
    assert [r["id"] for r in rows] == list(reversed(ids))  # newest first


@pytest.mark.asyncio
async def test_load_recent_episodes_filters_by_instance(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await db.append_episode(_make_ep("inst_a", "from_a"))
    await db.append_episode(_make_ep("inst_b", "from_b"))

    rows_a = await db.load_recent_episodes("inst_a")
    assert len(rows_a) == 1
    assert rows_a[0]["despertar_por"] == "from_a"
    assert rows_a[0]["instance_id"] == "inst_a"


@pytest.mark.asyncio
async def test_load_recent_episodes_respects_limit(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    for i in range(5):
        await db.append_episode(_make_ep("inst_personal", f"t{i}"))
    rows = await db.load_recent_episodes("inst_personal", n=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_dominio_activity_aggregates_per_brain(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    ep1 = _make_ep("inst_personal", "trigger_a")
    ep1.dominio = "temple"
    await db.append_episode(ep1)
    ep2 = _make_ep("inst_personal", "trigger_b")
    ep2.dominio = "temple"
    await db.append_episode(ep2)
    ep3 = _make_ep("inst_personal", "trigger_c")
    ep3.dominio = "guardian"
    await db.append_episode(ep3)
    # No dominio at all; must not blow up the grouping or appear as a key.
    await db.append_episode(_make_ep("inst_personal", "no_dominio"))

    activity = await db.dominio_activity("inst_personal")
    assert activity["temple"]["count"] == 2
    assert activity["guardian"]["count"] == 1
    assert activity["temple"]["last"]
    assert "" not in activity
    assert None not in activity


@pytest.mark.asyncio
async def test_dominio_activity_respects_since_cutoff(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    ep = _make_ep("inst_personal", "old")
    ep.dominio = "temple"
    ep.creado_en = "2000-01-01T00:00:00+00:00"
    await db.append_episode(ep)

    activity = await db.dominio_activity("inst_personal", since="2099-01-01T00:00:00+00:00")
    assert activity["temple"]["count"] == 0
    assert activity["temple"]["last"] == "2000-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_dominio_activity_empty_on_no_episodes(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    assert await db.dominio_activity("inst_personal") == {}


# ── tasks ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_and_load_pending_tasks(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    task_id = await db.add_task(
        "inst_personal", "sync_repo", "proyecto_X",
        params={"branch": "main"}, origen_trust=1, origen_actor="duenio",
    )
    assert isinstance(task_id, str) and task_id

    tasks = await db.load_pending_tasks("inst_personal")
    assert len(tasks) == 1
    assert tasks[0]["accion"] == "sync_repo"
    assert tasks[0]["params"] == {"branch": "main"}  # decoded back to dict
    assert tasks[0]["origen_actor"] == "duenio"


@pytest.mark.asyncio
async def test_load_pending_tasks_filters_by_instance(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await db.add_task("inst_a", "sync_repo", "x")
    await db.add_task("inst_b", "sync_repo", "y")
    assert len(await db.load_pending_tasks("inst_a")) == 1


@pytest.mark.asyncio
async def test_set_task_state_removes_from_pending(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    task_id = await db.add_task("inst_personal", "sync_repo", "x")
    await db.set_task_state("inst_personal", task_id, "hecha")
    assert await db.load_pending_tasks("inst_personal") == []


# ── jobs ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_and_update_job(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    job_id = await db.record_job("inst_personal", pid=4242, harness="opencode")
    await db.update_job(
        "inst_personal", job_id, estado="done", costo_usd=0.01,
        terminado_en="2026-01-01T00:00:00+00:00",
    )
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM jobs WHERE instance_id=? AND id=?",
            ("inst_personal", job_id),
        ) as cur:
            row = dict(await cur.fetchone())
    assert row["estado"] == "done"
    assert row["costo_usd"] == 0.01
    assert row["pid"] == 4242


# ── desired_state ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_and_get_desired_state(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await db.upsert_desired_state("inst_personal", "pc-casa", "proyecto_X", "abc123")
    await db.upsert_desired_state("inst_personal", "pc-casa", "proyecto_Y", "def456")
    state = await db.get_desired_state("inst_personal", "pc-casa")
    assert state == {"proyecto_X": "abc123", "proyecto_Y": "def456"}


@pytest.mark.asyncio
async def test_upsert_desired_state_overwrites(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await db.upsert_desired_state("inst_personal", "pc-casa", "proyecto_X", "abc123")
    await db.upsert_desired_state("inst_personal", "pc-casa", "proyecto_X", "zzz999")
    state = await db.get_desired_state("inst_personal", "pc-casa")
    assert state == {"proyecto_X": "zzz999"}


@pytest.mark.asyncio
async def test_get_desired_state_filters_by_instance_and_machine(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await db.upsert_desired_state("inst_a", "pc-casa", "k", "va")
    await db.upsert_desired_state("inst_b", "pc-casa", "k", "vb")
    await db.upsert_desired_state("inst_a", "pc-otra", "k", "vc")
    assert await db.get_desired_state("inst_a", "pc-casa") == {"k": "va"}


# ── approvals ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_load_and_resolve_approval(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    ap_id = await db.add_approval("inst_personal", "tarea_sensible", {"task": "t_001"})
    pending = await db.load_pending_approvals("inst_personal")
    assert len(pending) == 1
    assert pending[0]["payload"] == {"task": "t_001"}

    await db.resolve_approval("inst_personal", ap_id, "aprobada")
    assert await db.load_pending_approvals("inst_personal") == []


# ── sweep_deferred_tasks (resilience: retry / reject deferred tasks) ────────────

_OLD_TS = "2000-01-01T00:00:00+00:00"


async def _defer(db, monkeypatch, instance_id, task_id):
    """Mark ``task_id`` diferida with a stale ``updated_en`` (well past any backoff)."""
    with monkeypatch.context() as m:
        m.setattr(db, "_now", lambda: _OLD_TS)
        await db.set_task_state(instance_id, task_id, "diferida")


@pytest.mark.asyncio
async def test_sweep_deferred_tasks_respects_backoff_window(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    task_id = await db.add_task("inst_personal", "algo", "x", params={})
    # Deferred just now (updated_en ~= now); a large backoff window means it is
    # NOT yet due for retry.
    await db.set_task_state("inst_personal", task_id, "diferida")

    result = await db.sweep_deferred_tasks(
        "inst_personal", backoff_seconds=3600, max_retries=3
    )
    assert result == {"requeued": 0, "rejected_ids": []}
    tasks = await db.list_tasks("inst_personal")
    assert tasks[0]["estado"] == "diferida"


@pytest.mark.asyncio
async def test_sweep_deferred_tasks_requeues_after_backoff(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    task_id = await db.add_task("inst_personal", "algo", "x", params={"foo": "bar"})
    await _defer(db, monkeypatch, "inst_personal", task_id)

    result = await db.sweep_deferred_tasks(
        "inst_personal", backoff_seconds=1, max_retries=3
    )
    assert result["requeued"] == 1
    assert result["rejected_ids"] == []

    tasks = await db.list_tasks("inst_personal")
    assert tasks[0]["estado"] == "pendiente"
    assert tasks[0]["params"]["_retries"] == 1
    # The original params survive the requeue (only _retries is added/bumped).
    assert tasks[0]["params"]["foo"] == "bar"


@pytest.mark.asyncio
async def test_sweep_deferred_tasks_rejects_after_max_retries(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    task_id = await db.add_task("inst_personal", "algo", "x", params={})
    max_retries = 2

    # Exhaust the retries: each cycle defers, then the sweep requeues it.
    for expected_retries in range(1, max_retries + 1):
        await _defer(db, monkeypatch, "inst_personal", task_id)
        result = await db.sweep_deferred_tasks(
            "inst_personal", backoff_seconds=1, max_retries=max_retries
        )
        assert result["requeued"] == 1
        assert result["rejected_ids"] == []
        tasks = await db.list_tasks("inst_personal")
        assert tasks[0]["params"]["_retries"] == expected_retries

    # One more deferral: retries already at max_retries → terminal rejection.
    await _defer(db, monkeypatch, "inst_personal", task_id)
    result = await db.sweep_deferred_tasks(
        "inst_personal", backoff_seconds=1, max_retries=max_retries
    )
    assert result["requeued"] == 0
    assert result["rejected_ids"] == [task_id]

    tasks = await db.list_tasks("inst_personal")
    assert tasks[0]["estado"] == "rechazada"

    # A rejected task is terminal; a further sweep leaves it alone.
    result = await db.sweep_deferred_tasks(
        "inst_personal", backoff_seconds=1, max_retries=max_retries
    )
    assert result == {"requeued": 0, "rejected_ids": []}


@pytest.mark.asyncio
async def test_sweep_deferred_tasks_ignores_other_states_and_instances(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    pending_id = await db.add_task("inst_personal", "algo", "x", params={})
    done_id = await db.add_task("inst_personal", "algo", "y", params={})
    await db.set_task_state("inst_personal", done_id, "hecha")
    other_task = await db.add_task("inst_other", "algo", "z", params={})
    await _defer(db, monkeypatch, "inst_other", other_task)

    result = await db.sweep_deferred_tasks(
        "inst_personal", backoff_seconds=1, max_retries=3
    )
    assert result == {"requeued": 0, "rejected_ids": []}

    assert (await db.list_tasks("inst_personal", estado="pendiente"))[0]["id"] == pending_id
    assert (await db.list_tasks("inst_other", estado="diferida"))[0]["id"] == other_task
