"""Phase-0 Definition-of-Terminado — the end-to-end column, offline (§3-Fase 0).

This is the DoD test for Phase 0: "lanzas el timer → el PC arranca, hace check-in,
el cerebro reconcilia, corre sync_repo (gate de allowlist) y reporta; el episodio
explica *por qué*; matas el VPS a media tarea y al revivir continúa; cada fila
lleva instance_id; pytest verde con replay offline."

Fully offline, like ``tests/test_checkin.py``:
  - brain DB + signing key redirected to tmp via the 0.1/0.4 monkeypatch seams,
  - git mocked at ``actions._run`` (no real git, network, or LLM),
  - the local agent drives the FastAPI app through ``httpx.ASGITransport``.
"""

import importlib
import subprocess

import httpx
import pytest
import pytest_asyncio  # noqa: F401

INSTANCE = "inst_personal"
BASE = "http://brain"


# ── shared offline setup (mirrors test_checkin) ──────────────────────────────────

async def _setup(tmp_path, monkeypatch):
    """Redirect brain DB + signing key to tmp, init the DB, return the db module."""
    import lhassa.core.db as db
    import lhassa.security.signing as signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    await db.init_db()
    return db


def _fake_git(returncode=0, stdout="Updating abc..def456", stderr=""):
    """Fake ``actions._run`` returning a canned CompletedProcess (no real git)."""
    def _run(cmd):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
    return _run


def _make_repo(tmp_path, name="proyecto_X"):
    """A directory that looks like a git repo (has a .git marker)."""
    repo = tmp_path / name
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def _agent_against_app(monkeypatch):
    """Point the local agent's HTTP client at the FastAPI app via ASGITransport."""
    from lhassa.api import server
    import lhassa.remote.local_agent as la

    transport = httpx.ASGITransport(app=server.app)
    monkeypatch.setattr(
        la,
        "_make_client",
        lambda base_url: httpx.AsyncClient(transport=transport, base_url=base_url),
    )
    return la


async def _all_rows(db, table):
    """Raw rows of a table straight from disk (for instance_id / persistence checks)."""
    from lhassa._vendor import aiosqlite

    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(f"SELECT * FROM {table}") as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── the full DoD column, end to end ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_phase0_dod_end_to_end(tmp_path, monkeypatch):
    db = await _setup(tmp_path, monkeypatch)
    from lhassa.core import actions

    # Seed a desired_state diff: the brain wants the repo at commit A; the machine
    # reports it at B. The desired key IS the repo dir so the synthesized
    # sync_repo finds a real path to (mock-)pull.
    repo = _make_repo(tmp_path)
    await db.upsert_desired_state(INSTANCE, "pc-casa", str(repo), "A")
    monkeypatch.setattr(actions, "_run", _fake_git(stdout="Updating abc..def456"))

    la = _agent_against_app(monkeypatch)

    # The machine's heartbeat: check-in → ONE signed sync_repo task → allowlist
    # gate passes → mocked git runs → /report.
    reports = await la.run_once(
        BASE, "pc-casa", ["sync_repo"], current_state={str(repo): "B"}
    )
    assert len(reports) == 1, "reconciliation should emit exactly one task"
    rep = reports[0]
    assert rep["outcome"] == "hecha"          # gate passed AND action ran
    assert "sync_repo ok" in rep["detail"]    # the (mocked) git pull succeeded

    # /report marked the task done — nothing left pending.
    assert await db.load_pending_tasks(INSTANCE) == []
    done = [t for t in await _all_rows(db, "tasks") if t["estado"] == "hecha"]
    assert len(done) == 1
    assert done[0]["accion"] == "sync_repo"

    # The episode chain explains *why*: a check-in episode + a report episode,
    # each with non-empty despertar_por / accion.
    eps = await db.load_recent_episodes(INSTANCE)
    checkin_eps = [e for e in eps if e["despertar_por"].startswith("checkin:")]
    report_eps = [e for e in eps if e["despertar_por"].startswith("report:")]
    assert checkin_eps, "expected a check-in episode"
    assert report_eps, "expected a report episode"
    for e in checkin_eps + report_eps:
        assert e["despertar_por"]              # non-empty "why woke me"
        assert e["accion"]                     # non-empty "what I did"
    assert "checked in" in checkin_eps[0]["resultado"]
    assert "hecha" in report_eps[0]["resultado"]

    # Every written row carries the instance_id (org hook #1).
    for table in ("episodes", "tasks"):
        rows = await _all_rows(db, table)
        assert rows, f"{table} should have rows"
        assert all(r["instance_id"] == INSTANCE for r in rows), f"{table} missing instance_id"

    # ── survive-restart: kill the VPS, drop every in-memory handle, re-open the
    # brain DB fresh from disk, and assert the task state + episodes persisted. ──
    db_path = db.DB_PATH
    importlib.reload(db)                       # rebuild the module's in-memory state
    monkeypatch.setattr(db, "DB_PATH", db_path)  # point the fresh module at the same file

    survived_tasks = await db.load_pending_tasks(INSTANCE)
    assert survived_tasks == []                # still done after "restart"
    survived_done = [t for t in await _all_rows(db, "tasks") if t["estado"] == "hecha"]
    assert len(survived_done) == 1             # continuity from disk

    survived_eps = await db.load_recent_episodes(INSTANCE)
    assert any(e["despertar_por"].startswith("checkin:") for e in survived_eps)
    assert any(e["despertar_por"].startswith("report:") for e in survived_eps)
    # instance_id still on every row after the restart.
    for table in ("episodes", "tasks"):
        rows = await _all_rows(db, table)
        assert all(r["instance_id"] == INSTANCE for r in rows)


# ── direct beat()/step(): a pending task advances deterministically, no LLM ───────

@pytest.mark.asyncio
async def test_beat_advances_pending_task_deterministically(tmp_path, monkeypatch):
    db = await _setup(tmp_path, monkeypatch)
    from lhassa.core import actions, heartbeat

    repo = _make_repo(tmp_path)
    monkeypatch.setattr(actions, "_run", _fake_git(stdout="Updating abc..def"))

    await db.add_task(
        INSTANCE, "sync_repo", str(repo),
        params={"repo_dir": str(repo)}, origen_actor="duenio",
    )

    # One beat advances the brain exactly one tick — runs the fixed action, no LLM.
    summary = await heartbeat.beat(INSTANCE)
    assert summary["triggers"] == 1
    assert summary["actions"] == 1
    assert summary["deferred"] == 0

    # Task done; one episode explains it.
    assert await db.load_pending_tasks(INSTANCE) == []
    eps = await db.load_recent_episodes(INSTANCE)
    assert len(eps) == 1
    assert eps[0]["despertar_por"] == "task:sync_repo"
    assert eps[0]["accion"] == "sync_repo"
    assert eps[0]["instance_id"] == INSTANCE

    # A second beat finds nothing — the brain sleeps (idempotent, zero new episodes).
    summary2 = await heartbeat.beat(INSTANCE)
    assert summary2 == {"triggers": 0}
    assert len(await db.load_recent_episodes(INSTANCE)) == 1


# ── beat() with nothing pending sleeps (no episode, deterministic) ───────────────

@pytest.mark.asyncio
async def test_beat_with_nothing_pending_sleeps(tmp_path, monkeypatch):
    db = await _setup(tmp_path, monkeypatch)
    from lhassa.core import heartbeat

    summary = await heartbeat.beat(INSTANCE)
    assert summary == {"triggers": 0}
    assert await db.load_recent_episodes(INSTANCE) == []
