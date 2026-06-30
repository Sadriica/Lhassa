"""
Fixed, deterministic actions — the "do" half of "hace y duerme" (§2.4).

A trigger that maps to one of these runs in code with ZERO tokens: no model,
no judgment. The router/think branch (§2.4, Phase 2) is the *other* path; this
module is only the closed set of typed actions the brain can execute directly.

Each action is a callable ``(params: dict) -> str`` that returns a short outcome
string and NEVER raises — failures are captured and reported as text so the
episode always records *what happened*. The registry ``ACTIONS`` is the closed
set; ``watchlist.Trigger.maps_to_fixed_action`` checks membership against it.

First typed action: ``sync_repo`` (``git pull --ff-only``), per §2.5 — a check-in
reconciliation step, not a push.
"""

import subprocess
from pathlib import Path
from typing import Any, Callable


# ── subprocess seam (mockable) ───────────────────────────────────────────────────

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """
    Thin, mockable wrapper around the subprocess call so tests can monkeypatch
    ``lhassa.core.actions._run`` and stay fully offline (no real git/network).
    Never invoked with ``check=True``; callers inspect ``returncode`` themselves.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _short(text: str, limit: int = 300) -> str:
    """Collapse subprocess output to a single trimmed, length-capped line-ish blob."""
    text = (text or "").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


# ── actions (deterministic, never raise) ─────────────────────────────────────────

def sync_repo(params: dict) -> str:
    """
    Reconcile a local git repo to its remote with ``git -C <dir> pull --ff-only``
    (§2.5: ``git pull`` is reconciliation). Fast-forward only — never creates a
    merge commit, so a diverged repo fails loudly instead of guessing.

    ``params``:
        repo_dir / objetivo / dir / path — the repository directory (required).

    Returns a short outcome string. Guards a missing/blank dir and a dir that is
    not a git repo; captures stdout/stderr; never raises.
    """
    repo_dir = (
        params.get("repo_dir")
        or params.get("objetivo")
        or params.get("dir")
        or params.get("path")
        or ""
    ).strip() if isinstance(params, dict) else ""

    if not repo_dir:
        return "sync_repo: no repo_dir given"

    path = Path(repo_dir).expanduser()
    if not path.is_dir():
        return f"sync_repo: directory not found: {repo_dir}"
    if not (path / ".git").exists():
        return f"sync_repo: not a git repo: {repo_dir}"

    cmd = ["git", "-C", str(path), "pull", "--ff-only"]
    try:
        proc = _run(cmd)
    except FileNotFoundError:
        return "sync_repo: git not installed"
    except subprocess.TimeoutExpired:
        return f"sync_repo: timed out on {repo_dir}"
    except Exception as exc:  # defensive: an action must never raise
        return f"sync_repo: error on {repo_dir}: {exc}"

    if proc.returncode == 0:
        out = _short(proc.stdout) or "ok"
        return f"sync_repo ok ({repo_dir}): {out}"
    err = _short(proc.stderr) or _short(proc.stdout) or f"exit {proc.returncode}"
    return f"sync_repo failed ({repo_dir}): {err}"


# ── registry + dispatch ──────────────────────────────────────────────────────────

# The CLOSED set of fixed deterministic actions (no LLM). Membership here is what
# makes a Trigger "map to a fixed action". Grow this as new typed actions land.
ACTIONS: dict[str, Callable[[dict], str]] = {
    "sync_repo": sync_repo,
}


async def run(action: str, params: dict, episode: Any = None) -> str:
    """
    Dispatch ``action`` to the deterministic registry. Unknown action → safe
    error string (never raises). Attaches the action name to ``episode`` if one
    is supplied so observability reflects what ran. NO LLM is ever involved here.
    """
    if episode is not None:
        episode.accion = action

    fn = ACTIONS.get(action)
    if fn is None:
        return f"unknown action: {action!r}"

    try:
        return fn(params or {})
    except Exception as exc:  # belt-and-suspenders: dispatch must never raise
        return f"action {action!r} errored: {exc}"
