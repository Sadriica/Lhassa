"""Deterministic integrity action owned by the temple guardian."""

from __future__ import annotations

import json

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
        return json.dumps(
            {
                "run_id": result.get("run_id"),
                "status": result.get("status"),
                "findings_warn": int(result.get("findings_warn") or 0),
                "findings_critical": int(result.get("findings_critical") or 0),
            },
            sort_keys=True,
        )
    except Exception as exc:
        return json.dumps(
            {
                "error": f"{type(exc).__name__}: {exc}",
                "findings_critical": 1,
                "findings_warn": 0,
                "run_id": None,
                "status": "error",
            },
            sort_keys=True,
        )
