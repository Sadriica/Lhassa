# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local-git tool versioning — "versionado/rollback gratis" (§2.8).

The brief gets versioning and rollback for free by treating each self-written tool
as a repo/PR: a candidate lives on its own branch, **aprobación = merge de PR**, and
rollback is a git revert. We implement the *local-first* half of that here — a plain
git repo at ``config.tools_repo_dir`` — so the whole pipeline is offline-testable and
needs no GitHub token. The remote half (push the branch + open a real PR) is a
clearly-marked, token-gated hook that is **never** reached without ``github_token``.

Lifecycle of one tool, mirroring the four gates' graduation:
    create_candidate(name, files)  → commit on branch ``candidate/<name>``
    approve(name, branch=…)        → route a ``tool_graduation`` approval through
                                     :mod:`lhassa.security.approval`; on approve,
                                     **merge** the branch into ``main`` (= the merge)
    rollback(name, to=…)           → ``git revert`` to undo a graduated tool

Every git call goes through :func:`_git`, which **never raises** — a single failed
command is logged and surfaced as a non-zero result, so one bad invocation can't take
down the brain. When git itself is missing, the module degrades gracefully (each
entry point returns an empty/sentinel value instead of throwing).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from lhassa import config as _config
from lhassa.security import approval

# Identity used for the local repo's commits. Local-only, never pushed without a
# token; keeps `git commit` from failing on a machine with no global git identity.
_GIT_USER = "lhassa-tools"
_GIT_EMAIL = "tools@lhassa.local"


@dataclass
class GitResult:
    """Outcome of one git invocation. ``ok`` is ``returncode == 0``."""

    ok: bool
    code: int
    stdout: str
    stderr: str


def git_available() -> bool:
    """Whether a ``git`` binary is on PATH (the module degrades gracefully if not)."""
    return shutil.which("git") is not None


def _repo_dir() -> Path:
    return Path(_config.Config.tools_repo_dir).expanduser()


def _run_git(args: list[str], cwd: Path) -> GitResult:
    """Run one git command in ``cwd``. Never raises — failures come back as a result."""
    if not git_available():
        return GitResult(False, 127, "", "git not found on PATH")
    import subprocess

    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        res = GitResult(
            proc.returncode == 0, proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        )
        if not res.ok:
            logger.debug("git {} -> {} : {}", " ".join(args), res.code, res.stderr)
        return res
    except Exception as e:  # never let a single git call propagate
        logger.debug("git {} raised: {}", " ".join(args), e)
        return GitResult(False, -1, "", str(e))


async def _git(args: list[str], cwd: Optional[Path] = None) -> GitResult:
    """Async-shaped wrapper around :func:`_run_git`."""
    return _run_git(args, cwd or _repo_dir())


def _branch_for(name: str) -> str:
    return f"candidate/{name}"


async def init() -> Optional[Path]:
    """Idempotently ensure the tools repo exists with a base commit on ``main``.

    Returns the repo path, or ``None`` if git is unavailable (graceful: callers
    just get a no-op pipeline rather than an exception). Safe to call repeatedly.
    """
    repo = _repo_dir()
    if not git_available():
        logger.debug("versioning.init: git unavailable, skipping")
        return None
    repo.mkdir(parents=True, exist_ok=True)

    if not (repo / ".git").exists():
        if not (await _git(["init"], repo)).ok:
            return repo
        # Local identity so commits succeed on machines with no global git config.
        await _git(["config", "user.name", _GIT_USER], repo)
        await _git(["config", "user.email", _GIT_EMAIL], repo)
        await _git(["config", "commit.gpgsign", "false"], repo)
        # A base commit so candidate branches have something to branch from.
        (repo / ".lhassa-tools").write_text("lhassa auto-created tools repo\n")
        await _git(["add", ".lhassa-tools"], repo)
        await _git(["commit", "-m", "chore: init tools repo"], repo)
        # Normalise the default branch name to ``main`` regardless of git's default.
        await _git(["branch", "-M", "main"], repo)
    return repo


async def _head_sha(repo: Path) -> str:
    res = await _git(["rev-parse", "HEAD"], repo)
    return res.stdout if res.ok else ""


async def create_candidate(
    name: str, files: dict[str, str], *, branch: Optional[str] = None
) -> str:
    """Write a self-written tool's files and commit them on a candidate branch.

    Creates/points ``candidate/<name>`` (override with ``branch``) at ``main``, writes
    every ``files`` entry (path → content) relative to the repo, and commits. Returns
    ``"<branch>@<commit-sha>"`` so the caller knows exactly what to approve later, or
    ``""`` if git is unavailable.

    GitHub-remote hook (token-gated, NOT reached offline): when ``Config.github_token``
    is set this is where we would ALSO push ``branch`` and open a real PR via
    :mod:`lhassa.tools.github` — the "herramienta auto-creada como repo/PR" path.
    """
    repo = await init()
    if repo is None or not git_available():
        return ""
    br = branch or _branch_for(name)

    # Fresh candidate branch off main (a candidate is a new proposal each time).
    await _git(["checkout", "main"], repo)
    await _git(["checkout", "-B", br, "main"], repo)

    for rel, content in files.items():
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        await _git(["add", "--", rel], repo)

    commit = await _git(["commit", "-m", f"feat({name}): candidate tool"], repo)
    if not commit.ok:
        logger.debug("create_candidate({}): commit failed: {}", name, commit.stderr)

    sha = await _head_sha(repo)
    _maybe_open_remote_pr(name, br)  # token-gated no-op offline
    return f"{br}@{sha}" if sha else br


async def approve(
    name: str,
    *,
    branch: Optional[str] = None,
    instance_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    by_actor: str = "duenio",
) -> str:
    """Graduate a candidate: **approval = merge** (the brief's "aprobación = merge").

    The decision is routed through the approval queue (:mod:`lhassa.security.approval`):
    a ``tipo="tool_graduation"`` approval is created (unless ``approval_id`` names an
    existing pending one) and resolved as approved — that resolution IS the merge
    trigger. On approve we ``git merge --no-ff`` the candidate branch into ``main`` and
    return the merge commit sha (``""`` if git is unavailable or the merge failed).

    GitHub-remote hook (token-gated, NOT reached offline): with ``Config.github_token``
    set, "merge into main" would instead/also merge the open PR via
    :mod:`lhassa.tools.github`.
    """
    inst = instance_id or _config.Config.instance_id
    br = branch or _branch_for(name)

    # ── Route through the approval queue: this is where "merge = aprobación" lives.
    payload = {"accion": "tool_graduation", "tool": name, "branch": br, "actor_id": by_actor}
    if approval_id is None:
        approval_id = await approval.request_approval(
            inst, tipo="tool_graduation", payload=payload
        )
    await approval.resolve(inst, approval_id, approved=True, by_actor=by_actor)

    repo = await init()
    if repo is None or not git_available():
        return ""

    # On approve → merge the candidate branch into main.
    await _git(["checkout", "main"], repo)
    merge = await _git(
        ["merge", "--no-ff", "-m", f"merge: graduate tool {name}", br], repo
    )
    if not merge.ok:
        logger.debug("approve({}): merge failed: {}", name, merge.stderr)
        return ""
    sha = await _head_sha(repo)
    _maybe_merge_remote_pr(name, br)  # token-gated no-op offline
    return sha


async def rollback(name: str, *, to: Optional[str] = None) -> None:
    """Undo a graduated tool with ``git revert`` ("rollback con git").

    ``to`` is the commit to revert (default ``HEAD`` — the most recent graduation
    merge). Merge commits need ``-m 1``; if that does not apply (a plain commit) we
    fall back to a straight revert. Never raises — a failed revert is logged only.
    """
    repo = await init()
    if repo is None or not git_available():
        return
    target = to or "HEAD"
    await _git(["checkout", "main"], repo)
    # Merge commits require selecting the mainline parent; fall back for plain commits.
    res = await _git(["revert", "--no-edit", "-m", "1", target], repo)
    if not res.ok:
        res = await _git(["revert", "--no-edit", target], repo)
        if not res.ok:
            logger.debug("rollback({}): revert failed: {}", name, res.stderr)


# ── GitHub-remote hooks (token-gated; NEVER called without a token) ───────────
# These are the single plug-in points for the future remote path. They are no-ops
# unless ``Config.github_token`` is set, and even then they only LOG today — wiring
# them to real network calls in lhassa.tools.github is deliberately deferred so this
# module stays offline and never requires a token.

def _maybe_open_remote_pr(name: str, branch: str) -> None:
    if not _config.Config.github_token:
        return
    # FUTURE (token present): push ``branch`` and open a PR via lhassa.tools.github
    # — "herramienta auto-creada como repo/PR". Not wired yet (offline-first).
    logger.info("github_token set: remote PR for tool {} (branch {}) deferred", name, branch)


def _maybe_merge_remote_pr(name: str, branch: str) -> None:
    if not _config.Config.github_token:
        return
    # FUTURE (token present): merge the open PR via lhassa.tools.github — the remote
    # face of "aprobación = merge". Not wired yet (offline-first).
    logger.info("github_token set: remote merge for tool {} (branch {}) deferred", name, branch)
