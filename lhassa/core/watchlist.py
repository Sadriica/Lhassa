"""
The watchlist — the deterministic "what woke me?" check (§2.4).

Every tick, ``check()`` scans state in code with ZERO tokens and returns a list
of ``Trigger``s. Nothing to do → empty list → the brain sleeps. A trigger that
maps to a fixed action (``maps_to_fixed_action``) runs deterministically via
``actions.run``; one that does not is deferred to the Phase 2 router.

The brief (§2.4) lists the eventual trigger sources: a machine reported, a
file/repo changed, a task came due, a job finished, a threshold was crossed, a
message matched a rule. ``check()`` is structured as a small list of independent
check-funcs that each append Triggers, so new sources slot in without reshaping
the loop.

All check-funcs are ZERO-token and must never raise: a missing binary, a bad
health snapshot, or an absent repo is silently swallowed and logged at DEBUG.

Dedup guarantee: ``_from_finished_jobs``, ``_from_repo_changes``, and
``_from_health`` fire ONCE per state transition, not every beat. The
``watch_state`` table in the DB is the dedup store. Each check-func accepts a
``peek: bool`` parameter; when ``True``, it skips writes (read-only preview for
GET /api/watchlist). ``_from_pending_tasks`` is inherently non-repeating (DB
task is marked "hecha" after being acted on) so it needs no dedup.
"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from loguru import logger

from lhassa.config import Config
from lhassa.core import actions, db


@dataclass
class Trigger:
    """
    One reason the brain woke up, with everything needed to act on it.

    ``kind``         — the source/category ("task"; "repo", "job", "health", …).
    ``action``       — the typed action to take (closed set in ``actions.ACTIONS``
                       when fixed; otherwise a label the Phase 2 router resolves).
    ``objetivo``     — the target of the action (e.g. a repo/project name).
    ``params``       — action parameters.
    ``task_id``      — the originating task id, when the trigger came from a task.
    ``origen_actor`` — QUIÉN originated it (§2.6 hook for roles), if known.
    ``origen_trust`` — trust level of the originating task (0=MUNDO/1=INTERNO/2=HUMANO).
                       Carried through to dispatch so minted tasks degrade by contagion.
    ``due_at``       — ISO timestamp (from the task row) when this task was scheduled.
    ``recurrence``   — recurrence interval in SECONDS (string) from the task row.
    ``judgment``     — True when the task was minted with the judgment flag (any accion
                       allowed; the brain thinks about it rather than running a fixed action).
    """

    kind: str
    action: str
    objetivo: str = ""
    params: dict = field(default_factory=dict)
    task_id: Optional[str] = None
    origen_actor: Optional[str] = None
    origen_trust: int = 1  # default INTERNO
    due_at: Optional[str] = None
    recurrence: Optional[str] = None
    judgment: bool = False

    def maps_to_fixed_action(self) -> bool:
        """True iff ``action`` is in the closed deterministic set (no LLM needed).

        A judgment task always routes to the think path even when its accion name
        happens to match a fixed action — the owner explicitly requested thought.
        """
        if self.judgment:
            return False
        return self.action in actions.ACTIONS


# ── check functions (each appends Triggers; ZERO tokens) ─────────────────────────

async def _from_pending_tasks(
    instance_id: str, out: list[Trigger], peek: bool = False
) -> None:
    """Pending tasks in the brain DB → one Trigger per task (§2.4 source)."""
    for task in await db.load_pending_tasks(instance_id):
        out.append(
            Trigger(
                kind="task",
                action=task.get("accion", ""),
                objetivo=task.get("objetivo", "") or "",
                params=task.get("params", {}) or {},
                task_id=task.get("id"),
                origen_actor=task.get("origen_actor"),
                origen_trust=int(task.get("origen_trust") or 1),
                due_at=task.get("due_at"),
                recurrence=task.get("recurrence"),
                judgment=bool((task.get("params") or {}).get("_judgment")),
            )
        )


async def _from_finished_jobs(
    instance_id: str, out: list[Trigger], peek: bool = False
) -> None:
    """Finished harness jobs that nobody reviewed yet → one Trigger per job.

    Fires ONCE per job: the job row is marked ``acked=1`` (unless peek=True) so
    the next tick finds nothing new. The ``job_review`` action is NOT a fixed
    action — the judgment path reviews the outcome and may mint notify/facts via
    dispatch. The params include a ≤500-char result/status summary.
    """
    try:
        jobs = await db.load_unacked_finished_jobs(instance_id)
        for job in jobs:
            job_id = job.get("id", "")
            estado = job.get("estado", "")
            harness = job.get("harness", "")
            costo = job.get("costo_usd") or 0.0
            # Build a concise summary (≤500 chars) from what we know.
            summary = f"job {job_id} via {harness}: {estado} (${costo:.4f})"[:500]
            out.append(
                Trigger(
                    kind="job",
                    action="job_review",
                    objetivo=job_id,
                    params={"job_id": job_id, "estado": estado, "summary": summary},
                    origen_trust=1,  # INTERNO
                )
            )
            if not peek:
                try:
                    await db.ack_job(instance_id, job_id)
                except Exception as exc:
                    logger.debug(
                        "watchlist: could not ack job {}: {}", job_id, exc
                    )
    except Exception as exc:
        logger.debug("watchlist _from_finished_jobs error: {}", exc)


# ── repo-change helpers ───────────────────────────────────────────────────────

def _git_head(repo_path: str, timeout: int = 5) -> Optional[str]:
    """Return the current HEAD hash for ``repo_path`` via git rev-parse.

    Returns ``None`` on any error (missing git, not a repo, timeout). Offline-
    safe: never raises.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        pass
    return None


async def _from_repo_changes(
    instance_id: str, out: list[Trigger], peek: bool = False
) -> None:
    """Watched repos with a new HEAD commit → one Trigger per changed repo.

    Config knob: ``LHASSA_WATCH_REPOS`` (comma-separated absolute paths).
    Fires ONCE per new commit: the last-seen hash is stored in ``watch_state``
    under key ``repo:<path>``. The first observation seeds state without firing.
    Missing/unreadable repos are skipped silently.
    """
    try:
        raw = getattr(Config, "watch_repos", "") or ""
        paths = [p.strip() for p in raw.split(",") if p.strip()]
        if not paths:
            return
        for repo_path in paths:
            current = _git_head(repo_path)
            if current is None:
                continue  # unreadable repo — skip
            state_key = f"repo:{repo_path}"
            stored = await db.get_watch_state(instance_id, state_key)
            if stored is None:
                # First observation — seed, no trigger.
                if not peek:
                    await db.set_watch_state(instance_id, state_key, current)
                continue
            if stored == current:
                continue  # no change
            # HEAD changed — fire trigger.
            out.append(
                Trigger(
                    kind="repo",
                    action="repo_changed",
                    objetivo=repo_path,
                    params={"old": stored, "new": current},
                    origen_trust=1,
                )
            )
            if not peek:
                await db.set_watch_state(instance_id, state_key, current)
    except Exception as exc:
        logger.debug("watchlist _from_repo_changes error: {}", exc)


# ── health-digest helpers ─────────────────────────────────────────────────────

_HEALTH_DIGEST = Path.home() / ".lhassa" / "health" / "digest.json"

# Severity ordering (must match lhassa.health.checks._LEVELS).
_LEVEL_RANK = {"info": 0, "notice": 1, "warn": 2, "alert": 3}


def _health_level_rank(level: str) -> int:
    return _LEVEL_RANK.get(level, 0)


def _read_health_digest() -> Optional[dict]:
    """Read the latest health digest written by lhassa-health timer.

    Returns ``None`` if missing, unreadable, or malformed — never raises.
    """
    try:
        text = _HEALTH_DIGEST.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        return None


async def _from_health(
    instance_id: str, out: list[Trigger], peek: bool = False
) -> None:
    """Health digest transitions INTO warn/alert → one Trigger per transition.

    Fires once on the transition into a worse level. A recovering transition
    (warn → info) updates state silently without firing. The ``watch_state``
    key ``health:level`` stores the last-seen worst_level; the first observation
    seeds state without firing.
    """
    try:
        digest = _read_health_digest()
        if digest is None:
            return
        current_level = str(digest.get("worst_level", "info"))
        state_key = "health:level"
        stored = await db.get_watch_state(instance_id, state_key)
        if stored is None:
            # First observation — seed, no trigger.
            if not peek:
                await db.set_watch_state(instance_id, state_key, current_level)
            return
        if stored == current_level:
            return  # no change
        # Always update stored state.
        if not peek:
            await db.set_watch_state(instance_id, state_key, current_level)
        # Only fire on transition INTO a worse level (warn or alert).
        if _health_level_rank(current_level) > _health_level_rank(stored):
            summary = str(digest.get("ok", True))  # brief; the brain can read the file
            # Build a richer summary from the results dict.
            results = digest.get("results") or {}
            bad = [
                f"{name}: {r.get('summary', '')}"
                for name, r in results.items()
                if r.get("level") in ("warn", "alert")
            ]
            summary = "; ".join(bad) if bad else current_level
            out.append(
                Trigger(
                    kind="health",
                    action="health_alert",
                    params={"level": current_level, "summary": summary[:500]},
                    origen_trust=1,
                )
            )
    except Exception as exc:
        logger.debug("watchlist _from_health error: {}", exc)


# Ordered list of deterministic checks. Append more here (repo changed, job
# finished, threshold crossed, message matched) without touching ``check``.
_CHECKS: list[Callable[[str, list[Trigger], bool], Awaitable[None]]] = [
    _from_pending_tasks,
    _from_finished_jobs,
    _from_repo_changes,
    _from_health,
]


async def check(
    instance_id: Optional[str] = None,
    *,
    peek: bool = False,
) -> list[Trigger]:
    """
    Run every deterministic check and return the collected Triggers (ZERO tokens).
    No triggers → empty list (the brain sleeps). Defaults ``instance_id`` to the
    configured instance so callers can wake the watchlist with no arguments.

    ``peek=True`` skips all state writes (dedup marks, hash updates) so the call
    is read-only and safe to use for preview (GET /api/watchlist).
    """
    instance_id = instance_id or Config.instance_id
    triggers: list[Trigger] = []
    for check_fn in _CHECKS:
        try:
            await check_fn(instance_id, triggers, peek)
        except Exception as exc:
            logger.debug("watchlist check_fn {} error: {}", check_fn.__name__, exc)
    return triggers
