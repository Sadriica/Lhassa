# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic integrity action owned by the temple guardian."""

from __future__ import annotations


from lhassa.config import Config


async def check_integrity(params: dict) -> str:
    """Run the conformance manifest and return a compact structured result.

    ``config`` is an in-process override used by the watcher/tests. Recurring
    tasks omit it and resolve the ordinary environment-backed Config instead.
    The detailed evidence remains in the conformance tables; this action only
    returns the fields needed by the temple delivery and watcher reaction.
    """
    try:
        params = params if isinstance(params, dict) else {}
        config = params.get("config")
        if config is None:
            config = Config()
        trigger = str(params.get("trigger") or "temple")

        # Import by module at call time so tests and operators can instrument
        # the single canonical runner without maintaining a second seam.
        from lhassa.org import integrity

        result = await integrity.run_integrity(config, trigger=trigger)
        return _say(
            status=str(result.get("status") or "unknown"),
            critical=int(result.get("findings_critical") or 0),
            warnings=int(result.get("findings_warn") or 0),
            run_id=result.get("run_id"),
        )
    except Exception as exc:
        return f"check_integrity: error: {type(exc).__name__}: {exc}"


def _say(*, status: str, critical: int, warnings: int, run_id) -> str:
    """One sentence a person can read — the actionpack convention.

    This used to return ``json.dumps(...)`` because the watcher parsed it. That
    payload travelled all the way to the owner's screen as a raw dict, which is
    what a notification should never be: the machine-readable copy lives in the
    ``conformance_runs`` table, and the watcher reads it from there. Siblings
    like ``curate_skills`` and ``distill_memory`` already answer this way.
    """
    where = f" (run {run_id})" if run_id else ""
    if status == "pass":
        return f"check_integrity: every contract passed{where}"
    bits = []
    if critical:
        bits.append(f"{critical} critical")
    if warnings:
        bits.append(f"{warnings} warning")
    detail = " and ".join(bits) if bits else "no findings recorded"
    return f"check_integrity: {status} — {detail}{where}"
