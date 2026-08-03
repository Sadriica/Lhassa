"""Tests for Phase-0 slice 0.3 — the multi-machine "check-in, not push" loop.

Fully offline: the brain DB and signing key are redirected to tmp dirs via
monkeypatch (the 0.1/0.4 seams), git is mocked at ``actions._run``, and the local
agent talks to the FastAPI app through ``httpx.ASGITransport`` (no live network).

Covered:
  - reconciliation: a desired repo at commit A, machine at B → ONE signed
    sync_repo task; the brain wrote a check-in episode.
  - the local agent's allowlist gate ACCEPTS a properly-signed task (runs it,
    mocked git) and /report marks it done + writes an outcome episode.
  - a TAMPERED signature is REJECTED by the gate (not executed; "rechazada").
  - no desired/pending diffs → {"tasks": []}.
  - instance isolation holds.
"""

import subprocess

import httpx
import pytest
import pytest_asyncio  # noqa: F401


# ── shared offline setup ─────────────────────────────────────────────────────────

async def _setup(tmp_path, monkeypatch):
    """Redirect brain DB + signing key to tmp, init the DB, return the modules."""
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
    return la, server


BASE = "http://brain"


# ── reconciliation: desired ≠ current → one signed sync_repo task ────────────────

@pytest.mark.asyncio
async def test_checkin_reconciles_one_sync_repo_task(tmp_path, monkeypatch):
    db = await _setup(tmp_path, monkeypatch)
    from lhassa.api import server

    # Brain wants proyecto_X at commit A; the machine reports it at B.
    await db.upsert_desired_state("inst_personal", "pc-casa", "proyecto_X", "A")

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.post(
            "/checkin",
            json={
                "machine_id": "pc-casa",
                "capabilities": ["sync_repo"],
                "current_state": {"proyecto_X": "B"},
            },
        )
    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    t = tasks[0]
    assert t["accion"] == "sync_repo"
    assert t["objetivo"] == "proyecto_X"
    assert t["firma"].startswith("ed25519:")
    assert t["origen"]["trust"] == 1  # INTERNO
    assert t["origen"]["actor_id"] == "brain"

    # The signed task verifies against the brain's public key.
    from lhassa.core.task import Task
    from lhassa.security import signing
    assert signing.verify_task(Task.from_db_row(t), signing.load_public_key())

    # The brain recorded a check-in episode explaining what it emitted.
    eps = await db.load_recent_episodes("inst_personal")
    assert any(
        e["despertar_por"] == "checkin:pc-casa" and "checked in" in e["resultado"]
        for e in eps
    )


# ── no diffs → empty task list ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_checkin_no_diffs_returns_empty(tmp_path, monkeypatch):
    db = await _setup(tmp_path, monkeypatch)
    from lhassa.api import server

    await db.upsert_desired_state("inst_personal", "pc-casa", "proyecto_X", "A")

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.post(
            "/checkin",
            json={
                "machine_id": "pc-casa",
                "capabilities": ["sync_repo"],
                "current_state": {"proyecto_X": "A"},  # already at desired
            },
        )
    assert resp.status_code == 200
    assert resp.json() == {"tasks": []}


# ── instance isolation: another instance's desired_state is invisible ────────────

@pytest.mark.asyncio
async def test_checkin_respects_instance_isolation(tmp_path, monkeypatch):
    db = await _setup(tmp_path, monkeypatch)
    from lhassa.api import server

    # Desired state lives under a DIFFERENT instance than the server reconciles for.
    await db.upsert_desired_state("inst_other", "pc-casa", "proyecto_X", "A")

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.post(
            "/checkin",
            json={
                "machine_id": "pc-casa",
                "capabilities": ["sync_repo"],
                "current_state": {"proyecto_X": "B"},
            },
        )
    assert resp.json()["tasks"] == []  # inst_personal sees nothing of inst_other


# ── full loop: gate ACCEPTS a signed task, runs it, /report marks it done ────────

@pytest.mark.asyncio
async def test_agent_runs_signed_task_and_reports_done(tmp_path, monkeypatch):
    db = await _setup(tmp_path, monkeypatch)
    from lhassa.core import actions

    repo = _make_repo(tmp_path)
    # Desired key IS the repo dir so the synthesized sync_repo finds a real path.
    await db.upsert_desired_state("inst_personal", "pc-casa", str(repo), "A")
    monkeypatch.setattr(actions, "_run", _fake_git(stdout="Updating abc..def456"))

    la, _ = _agent_against_app(monkeypatch)

    reports = await la.run_once(
        BASE, "pc-casa", ["sync_repo"], current_state={str(repo): "B"}
    )
    assert len(reports) == 1
    rep = reports[0]
    assert rep["outcome"] == "hecha"
    assert "sync_repo ok" in rep["detail"]

    # /report marked the task done and wrote an outcome episode.
    assert await db.load_pending_tasks("inst_personal") == []
    eps = await db.load_recent_episodes("inst_personal")
    assert any(e["despertar_por"].startswith("report:") and "hecha" in e["resultado"] for e in eps)


# ── tampered signature is REJECTED by the gate (never executed) ──────────────────

@pytest.mark.asyncio
async def test_tampered_signature_is_rejected_and_not_executed(tmp_path, monkeypatch):
    db = await _setup(tmp_path, monkeypatch)
    from lhassa.core import actions

    repo = _make_repo(tmp_path)
    await db.upsert_desired_state("inst_personal", "pc-casa", str(repo), "A")

    # Track whether git is ever invoked; it must NOT be for a rejected task.
    invoked = {"git": False}

    def _tracking_run(cmd):
        invoked["git"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(actions, "_run", _tracking_run)

    la, _ = _agent_against_app(monkeypatch)

    tasks = await la.check_in(BASE, "pc-casa", ["sync_repo"], {str(repo): "B"})
    assert len(tasks) == 1
    # Tamper: flip the last hex nibble of the signature.
    bad = tasks[0]
    bad.firma = bad.firma[:-1] + ("0" if bad.firma[-1] != "0" else "1")

    reports = await la.run_tasks([bad])
    assert reports[0]["outcome"] == "rechazada"
    assert "signature" in reports[0]["detail"].lower()
    assert invoked["git"] is False  # never executed


# ── /report on its own marks state + writes an episode ───────────────────────────

@pytest.mark.asyncio
async def test_report_marks_state_and_writes_episode(tmp_path, monkeypatch):
    db = await _setup(tmp_path, monkeypatch)
    from lhassa.api import server

    task_id = await db.add_task("inst_personal", "sync_repo", "proyecto_X")

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.post(
            "/report",
            json={"task_id": task_id, "outcome": "hecha", "detail": "ff a def456"},
        )
    assert resp.json() == {"ok": True}
    assert await db.load_pending_tasks("inst_personal") == []
    eps = await db.load_recent_episodes("inst_personal")
    assert any(e["despertar_por"] == f"report:{task_id}" for e in eps)
