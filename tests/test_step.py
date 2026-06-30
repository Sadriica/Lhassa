"""
Tests for the Phase 0 "hace y duerme" loop — step + watchlist + actions.

Fully offline (replay): a tmp brain DB via monkeypatched ``db.DB_PATH`` and a
monkeypatched git subprocess. No live network, LLM, or git.
"""

import pytest
import pytest_asyncio  # noqa: F401


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


def _fake_git(returncode=0, stdout="Already up to date.", stderr=""):
    """Build a fake ``actions._run`` returning a canned CompletedProcess."""
    import subprocess

    def _run(cmd):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return _run


def _make_repo(tmp_path):
    """Create a directory that looks like a git repo (has a .git marker)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


# ── no triggers → sleep ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_step_no_pending_tasks_sleeps(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step

    summary = await step.step("inst_personal")
    assert summary == {"triggers": 0}
    # No episode written.
    assert await db.load_recent_episodes("inst_personal") == []


# ── pending sync_repo task → runs it, writes one episode, marks done ─────────────

@pytest.mark.asyncio
async def test_step_runs_pending_sync_repo(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import actions, step

    repo = _make_repo(tmp_path)
    monkeypatch.setattr(actions, "_run", _fake_git(stdout="Updating abc..def"))

    task_id = await db.add_task(
        "inst_personal", "sync_repo", "proyecto_X",
        params={"repo_dir": str(repo)}, origen_actor="duenio",
    )

    summary = await step.step("inst_personal")
    assert summary["triggers"] == 1
    assert summary["actions"] == 1
    assert summary["deferred"] == 0

    # Exactly one episode, explaining why (despertar_por) and what (accion).
    eps = await db.load_recent_episodes("inst_personal")
    assert len(eps) == 1
    ep = eps[0]
    assert ep["despertar_por"] == "task:sync_repo"
    assert ep["accion"] == "sync_repo"
    assert ep["decision"] == "sync_repo"
    assert "sync_repo ok" in ep["resultado"]
    assert "Updating abc..def" in ep["resultado"]

    # Task marked done.
    assert await db.load_pending_tasks("inst_personal") == []

    # A second step finds nothing and writes no further episode.
    summary2 = await step.step("inst_personal")
    assert summary2 == {"triggers": 0}
    assert len(await db.load_recent_episodes("inst_personal")) == 1

    # Sanity: which task was completed.
    assert task_id


# ── instance isolation ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_step_respects_instance_isolation(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import actions, step

    repo = _make_repo(tmp_path)
    monkeypatch.setattr(actions, "_run", _fake_git())

    # Task lives under a *different* instance.
    await db.add_task("inst_other", "sync_repo", "x", params={"repo_dir": str(repo)})

    summary = await step.step("inst_personal")
    assert summary == {"triggers": 0}
    assert await db.load_recent_episodes("inst_personal") == []

    # The other instance still sees its task pending (we never touched it).
    assert len(await db.load_pending_tasks("inst_other")) == 1


# ── unknown action → safe episode, no raise ──────────────────────────────────────

@pytest.mark.asyncio
async def test_step_unknown_action_is_deferred_safely(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import step

    await db.add_task("inst_personal", "teleport", "marte", params={})

    summary = await step.step("inst_personal")  # must not raise
    assert summary["triggers"] == 1
    assert summary["actions"] == 0
    assert summary["deferred"] == 1

    eps = await db.load_recent_episodes("inst_personal")
    assert len(eps) == 1
    assert eps[0]["decision"] == step.DEFERRED_DECISION
    assert eps[0]["accion"] == "teleport"

    # Not re-fired on the next tick (marked diferida, not pendiente).
    assert await db.load_pending_tasks("inst_personal") == []
    summary2 = await step.step("inst_personal")
    assert summary2 == {"triggers": 0}


# ── watchlist.check ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watchlist_check_maps_pending_tasks(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import watchlist

    assert await watchlist.check("inst_personal") == []

    await db.add_task(
        "inst_personal", "sync_repo", "proyecto_X",
        params={"repo_dir": "/tmp/x"}, origen_actor="duenio",
    )
    triggers = await watchlist.check("inst_personal")
    assert len(triggers) == 1
    t = triggers[0]
    assert t.kind == "task"
    assert t.action == "sync_repo"
    assert t.objetivo == "proyecto_X"
    assert t.params == {"repo_dir": "/tmp/x"}
    assert t.origen_actor == "duenio"
    assert t.task_id is not None
    assert t.maps_to_fixed_action() is True


def test_trigger_unknown_action_is_not_fixed():
    from lhassa.core.watchlist import Trigger
    t = Trigger(kind="task", action="teleport")
    assert t.maps_to_fixed_action() is False


# ── actions.sync_repo guards (never raise) ───────────────────────────────────────

def test_sync_repo_missing_dir():
    from lhassa.core import actions
    assert "no repo_dir" in actions.sync_repo({})


def test_sync_repo_dir_not_found(tmp_path):
    from lhassa.core import actions
    out = actions.sync_repo({"repo_dir": str(tmp_path / "nope")})
    assert "directory not found" in out


def test_sync_repo_not_a_git_repo(tmp_path):
    from lhassa.core import actions
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "not a git repo" in actions.sync_repo({"repo_dir": str(plain)})


def test_sync_repo_reports_git_failure(tmp_path, monkeypatch):
    from lhassa.core import actions
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(
        actions, "_run", _fake_git(returncode=1, stdout="", stderr="not a ff"),
    )
    out = actions.sync_repo({"repo_dir": str(repo)})
    assert "sync_repo failed" in out
    assert "not a ff" in out


def test_sync_repo_uses_objetivo_as_dir(tmp_path, monkeypatch):
    from lhassa.core import actions
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(actions, "_run", _fake_git(stdout="ok"))
    out = actions.sync_repo({"objetivo": str(repo)})
    assert "sync_repo ok" in out


# ── actions.run dispatch ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_actions_run_unknown_is_safe():
    from lhassa.core import actions
    out = await actions.run("does_not_exist", {})
    assert "unknown action" in out


@pytest.mark.asyncio
async def test_actions_run_attaches_action_to_episode(tmp_path, monkeypatch):
    from lhassa.core import actions
    from lhassa.core.episode import Episode

    repo = _make_repo(tmp_path)
    monkeypatch.setattr(actions, "_run", _fake_git(stdout="ok"))
    ep = Episode.begin(despertar_por="task:sync_repo", instance_id="inst_personal")
    await actions.run("sync_repo", {"repo_dir": str(repo)}, ep)
    assert ep.accion == "sync_repo"
