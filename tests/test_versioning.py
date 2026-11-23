"""Tests for lhassa.agents.versioning; local-git tool versioning.

Offline: a real local git repo in a tmp dir, the brain DB redirected to a tmp file,
no GitHub remote/network. Exercises init → create_candidate → approve (merge) →
rollback (revert), and that approve routes through the approval queue.
"""

import shutil

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.agents import versioning

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git not installed")


@pytest_asyncio.fixture
async def tools_repo(tmp_path, monkeypatch):
    """Redirect the tools repo + the brain DB to tmp files."""
    import lhassa.core.db as db
    from lhassa.config import Config

    repo = tmp_path / "tools"
    monkeypatch.setattr(Config, "tools_repo_dir", str(repo))
    monkeypatch.setattr(Config, "github_token", "")  # offline: no remote path
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return repo


def _git(repo, *args):
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True
    )


@needs_git
@pytest.mark.asyncio
async def test_init_is_idempotent(tools_repo):
    repo = await versioning.init()
    assert repo is not None
    assert (repo / ".git").exists()
    # Calling again must not blow up or re-init.
    repo2 = await versioning.init()
    assert repo2 == repo


@needs_git
@pytest.mark.asyncio
async def test_create_candidate_commits_on_candidate_branch(tools_repo):
    ref = await versioning.create_candidate(
        "speedup", {"speedup.py": "print('fast')\n"}
    )
    assert ref.startswith("candidate/speedup")
    repo = tools_repo
    # The branch exists and carries the file; main does NOT yet.
    branches = _git(repo, "branch", "--list", "candidate/speedup").stdout
    assert "candidate/speedup" in branches
    on_branch = _git(repo, "show", "candidate/speedup:speedup.py").stdout
    assert "print('fast')" in on_branch
    on_main = _git(repo, "show", "main:speedup.py")
    assert on_main.returncode != 0  # not on main until approved


@needs_git
@pytest.mark.asyncio
async def test_approve_merges_into_main_and_routes_through_approval(tools_repo):
    import lhassa.core.db as db
    from lhassa.config import Config

    await versioning.create_candidate("speedup", {"speedup.py": "print('fast')\n"})
    merge = await versioning.approve("speedup", branch="candidate/speedup")
    assert merge  # merge commit sha

    repo = tools_repo
    on_main = _git(repo, "show", "main:speedup.py").stdout
    assert "print('fast')" in on_main  # content present on main after merge

    # The decision was routed through the approval queue as a tool_graduation that
    # got resolved (no longer pending).
    pending = await db.load_pending_approvals(Config.instance_id)
    assert all(a.get("tipo") != "tool_graduation" or a.get("estado") != "pendiente"
               for a in pending)
    # And a resolved tool_graduation approval exists.
    from lhassa._vendor import aiosqlite

    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT tipo, estado FROM approvals WHERE tipo = 'tool_graduation'"
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    assert rows and rows[0]["estado"] == "aprobada"


@needs_git
@pytest.mark.asyncio
async def test_rollback_reverts_graduated_tool(tools_repo):
    await versioning.create_candidate("speedup", {"speedup.py": "print('fast')\n"})
    await versioning.approve("speedup", branch="candidate/speedup")
    repo = tools_repo
    assert _git(repo, "show", "main:speedup.py").returncode == 0  # present

    await versioning.rollback("speedup")
    # After reverting the graduation merge, the file is gone from main's tree.
    assert _git(repo, "show", "main:speedup.py").returncode != 0


@needs_git
@pytest.mark.asyncio
async def test_full_pipeline_init_create_approve_rollback(tools_repo):
    assert await versioning.init() is not None
    ref = await versioning.create_candidate("opt", {"opt.txt": "v1\n"})
    assert "candidate/opt" in ref
    merge = await versioning.approve("opt", branch="candidate/opt")
    assert merge
    await versioning.rollback("opt")  # must not raise


@pytest.mark.asyncio
async def test_graceful_when_git_missing(tools_repo, monkeypatch):
    # Simulate git not on PATH: every entry point degrades, none raises.
    monkeypatch.setattr(versioning, "git_available", lambda: False)
    assert await versioning.init() is None
    assert await versioning.create_candidate("x", {"a.txt": "1"}) == ""
    # approve still resolves the approval (queue) but returns "" with no git.
    assert await versioning.approve("x", branch="candidate/x") == ""
    await versioning.rollback("x")  # no-op, no raise
