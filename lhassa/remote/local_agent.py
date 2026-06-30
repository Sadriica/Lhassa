"""The thin local agent — the machine's half of "check-in, not push" (§2.5).

Runs ON a machine, talks OUTWARD to the brain over HTTP. The flow per heartbeat:

    check_in  → POST /checkin, get back signed Tasks
    run_tasks → for each Task, the allowlist GATE (§2.6, on the machine) verifies
                the signature + trust + allowlist BEFORE anything runs; denied
                tasks are reported "rechazada" and never executed
    report    → POST /report each outcome

``run_once`` chains the three; it is the agent's heartbeat-side counterpart to the
brain's ``step()``. Everything is async (httpx) and NOTHING raises on a single
task failure — a heartbeat must survive a bad task and keep going.

The deny-by-default gate lives here, on the machine, on purpose: the VPS is the
weak link (§2.6), so the machine does not fully trust it. A signed Task is the
only thing that runs, and only if the local allowlist says so.

Test seam: ``_make_client`` is the single place an ``httpx.AsyncClient`` is built,
so tests monkeypatch it to drive an ``httpx.ASGITransport`` against the FastAPI
app — fully offline, no live network.
"""

from typing import Any

import httpx

from lhassa.core import actions
from lhassa.core.task import Task
from lhassa.security import allowlist

# Result-string fragments that mark a deterministic action as having failed.
# Actions never raise (they return a short outcome string), so we read the text.
_FAILURE_MARKERS = (
    "failed",
    "error",
    "not installed",
    "not found",
    "not a git",
    "no repo_dir",
    "timed out",
    "unknown action",
)


def _make_client(base_url: str) -> httpx.AsyncClient:
    """Build the HTTP client used to reach the brain.

    The single construction seam: tests monkeypatch this to return a client built
    with ``httpx.ASGITransport(app=...)`` so the whole loop runs offline against
    the FastAPI app with no sockets.
    """
    return httpx.AsyncClient(base_url=base_url)


def _outcome_for(result: str) -> str:
    """Map a deterministic action's outcome string to 'hecha' / 'fallida'."""
    low = (result or "").lower()
    return "fallida" if any(m in low for m in _FAILURE_MARKERS) else "hecha"


async def check_in(
    base_url: str,
    machine_id: str,
    capabilities: list[str],
    current_state: dict,
    *,
    public_key: Any = None,
) -> list[Task]:
    """POST /checkin and return the brain's Tasks as ``Task`` objects.

    ``public_key`` is accepted for symmetry with the rest of the loop; the brain
    decides what to emit, so it is not used to fetch — the gate in ``run_tasks``
    is where the signature is checked.
    """
    payload = {
        "machine_id": machine_id,
        "capabilities": list(capabilities or []),
        "current_state": current_state or {},
    }
    async with _make_client(base_url) as client:
        resp = await client.post("/checkin", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return [Task.from_db_row(t) for t in data.get("tasks", [])]


async def run_tasks(tasks: list[Task], *, public_key: Any = None) -> list[dict]:
    """Gate each Task with the allowlist, run the allowed ones, build reports.

    Deny-by-default: ``allowlist.allow`` verifies signature + trust + allowlist.
    A denied task is reported ``rechazada`` and is NEVER executed. An allowed task
    runs via the deterministic action registry. Never raises on a single task.
    """
    reports: list[dict] = []
    for task in tasks:
        decision = allowlist.allow(task, public_key=public_key)
        if not decision.allowed:
            reports.append(
                {"task_id": task.id, "outcome": "rechazada", "detail": decision.reason}
            )
            continue
        try:
            result = await actions.run(task.accion, task.params)
            reports.append(
                {"task_id": task.id, "outcome": _outcome_for(result), "detail": result}
            )
        except Exception as exc:  # an action shouldn't raise, but a heartbeat must survive it
            reports.append(
                {"task_id": task.id, "outcome": "fallida", "detail": f"agent error: {exc}"}
            )
    return reports


async def report(base_url: str, reports: list[dict]) -> None:
    """POST /report for each report. Never raises on a single failed post."""
    if not reports:
        return
    async with _make_client(base_url) as client:
        for r in reports:
            try:
                resp = await client.post("/report", json=r)
                resp.raise_for_status()
            except Exception:
                # A heartbeat keeps going even if one report fails to land.
                continue


async def run_once(
    base_url: str,
    machine_id: str,
    capabilities: list[str],
    current_state: dict,
    *,
    public_key: Any = None,
) -> list[dict]:
    """One heartbeat: check_in → run_tasks (allowlist-gated) → report.

    Returns the reports it posted (handy for observability/tests). The brain-side
    counterpart is ``core.step.step``.
    """
    tasks = await check_in(
        base_url, machine_id, capabilities, current_state, public_key=public_key
    )
    reports = await run_tasks(tasks, public_key=public_key)
    await report(base_url, reports)
    return reports
