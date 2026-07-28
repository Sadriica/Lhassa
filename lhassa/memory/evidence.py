"""Evidence vocabulary — the oh-my-hermes-inspired claim ladder over a
run-or-task-shaped row. A PURE DERIVED read, no new storage: everything it
returns is computed off columns already on ``runs``/``tasks``/``run_steps``
(``status``/``estado``) plus whatever ``context`` a caller chooses to pass.

The evidence contract (three rungs, humblest-first)::

    "prepared" — intent recorded, no outcome yet. ``status``/``estado`` is one
                 of the in-flight states: queued / running / pendiente /
                 esperando_aprobacion. This is the DEFAULT for anything
                 unrecognized — an unknown shape gets the humblest claim, never
                 an inflated one.

    "observed" — an episode records what happened: ``status``/``estado`` is a
                 finished state (completed/finished/hecha/failed/fallida/
                 cancelled/...). The row asserts an outcome, but nothing has
                 independently checked it.

    "verified" — observed AND independently corroborated: either
                 (a) the row/its ``raw`` payload carries a passing verify
                     outcome (a ``VerifyResult``-shaped dict/object with
                     ``passed`` truthy, or a nested ``verify``/``verification``
                     key with ``passed``/``ok`` truthy), or
                 (b) the caller-supplied ``context`` names an approval
                     resolved ``aprobada`` that is linked to this row (by
                     ``run_id``/``task_id`` matching the row's own id).

``context`` is optional and additive: callers who already loaded approvals or
verification results for a batch of rows can pass them once
(``{"approvals": [...], "verifications": [...]}``) instead of this module
re-querying anything — this stays a pure function, no I/O.

Never raises: any exception, missing key, or odd shape degrades to
``"prepared"`` — the humblest claim is always the safe fallback.
"""

from __future__ import annotations

from typing import Any, Optional

_PREPARED_STATUSES = {
    "queued",
    "running",
    "pendiente",
    "esperando_aprobacion",
    "waiting_approval",
    "waiting_retry",
}

_OBSERVED_STATUSES = {
    "completed",
    "complete",
    "finished",
    "hecha",
    "done",
    "failed",
    "fallida",
    "error",
    "cancelled",
    "canceled",
    "rechazada",
    "timed_out",
    "abandoned",
}


def _status_of(row: dict) -> str:
    value = row.get("status") if "status" in row else row.get("estado")
    return str(value or "").strip().lower()


def _passed(candidate: Any) -> Optional[bool]:
    """Read a truthy/falsy ``passed``/``ok`` off a verify-result-shaped value.
    Returns ``None`` if ``candidate`` doesn't look like one."""
    if candidate is None:
        return None
    if hasattr(candidate, "passed"):
        try:
            return bool(candidate.passed)
        except Exception:
            return None
    if isinstance(candidate, dict):
        for key in ("passed", "ok"):
            if key in candidate:
                try:
                    return bool(candidate[key])
                except Exception:
                    return None
    return None


def _row_has_passing_verify(row: dict) -> bool:
    for key in ("verify", "verification", "verify_result"):
        result = _passed(row.get(key))
        if result:
            return True
    raw = row.get("raw")
    if isinstance(raw, dict):
        for key in ("verify", "verification", "verify_result"):
            result = _passed(raw.get(key))
            if result:
                return True
    return False


def _context_has_verified_approval(row: dict, context: dict) -> bool:
    approvals = context.get("approvals") or []
    if not approvals:
        return False
    row_id = row.get("id") or row.get("run_id") or row.get("task_id")
    if not row_id:
        return False
    for approval in approvals:
        try:
            if str(approval.get("estado") or "").strip().lower() != "aprobada":
                continue
            payload = approval.get("payload") or {}
            linked = (
                payload.get("run_id")
                or payload.get("task_id")
                or approval.get("run_id")
                or approval.get("task_id")
            )
            if linked and str(linked) == str(row_id):
                return True
        except Exception:
            continue
    return False


def _context_has_passing_verification(row: dict, context: dict) -> bool:
    verifications = context.get("verifications") or []
    if not verifications:
        return False
    row_id = row.get("id") or row.get("run_id") or row.get("task_id")
    for entry in verifications:
        try:
            if entry.get("run_id") and row_id and str(entry["run_id"]) != str(row_id):
                continue
            if entry.get("task_id") and row_id and str(entry["task_id"]) != str(row_id):
                continue
            if _passed(entry) or _passed(entry.get("result")):
                return True
        except Exception:
            continue
    return False


def evidence_state(row: dict, context: Optional[dict] = None) -> str:
    """Derive ``"prepared"`` | ``"observed"`` | ``"verified"`` for one
    run-or-task-shaped ``row``. See the module docstring for the contract.
    Never raises — an unrecognized shape returns ``"prepared"``.
    """
    try:
        if not isinstance(row, dict):
            return "prepared"
        status = _status_of(row)
        if status in _PREPARED_STATUSES or not status:
            return "prepared"
        if status not in _OBSERVED_STATUSES:
            # Unrecognized status string: neither known in-flight nor known
            # finished — humblest claim.
            return "prepared"

        # observed. Look for independent corroboration to promote to verified.
        if _row_has_passing_verify(row):
            return "verified"
        if context:
            if _context_has_passing_verification(row, context):
                return "verified"
            if _context_has_verified_approval(row, context):
                return "verified"
        return "observed"
    except Exception:
        return "prepared"
