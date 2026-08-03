# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runtime settings routes — the panel's editable allowlist (§ settings service).

GET the effective read model (current value + default + overridden, masked) and
PATCH a single key (or {updates:{...}}) → persisted to ~/.lhassa/settings.env and
applied live. Never 500: a bad key/value is a 400 with the per-key reason.

Mounted by the integrator with ``app.include_router(config_routes.router)``.
"""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/api/config")
async def get_config():
    """The effective settings read model for the dashboard Settings panel."""
    from lhassa import settings

    return settings.effective()


@router.patch("/api/config")
async def patch_config(payload: dict):
    """Change settings. Body: ``{key, value}`` or ``{updates: {key: value, ...}}``.

    Returns the updated effective model plus per-key results and a combined
    ``restart_hint``. A bad key/value yields a 400 (nothing is half-applied beyond
    the keys that validated).
    """
    from lhassa import settings

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="config payload must be a JSON object.")

    updates = payload.get("updates")
    if updates is None:
        if "key" not in payload:
            raise HTTPException(status_code=400, detail="'key' (and 'value') are required.")
        updates = {payload["key"]: payload.get("value")}
    if not isinstance(updates, dict) or not updates:
        raise HTTPException(status_code=400, detail="'updates' must be a non-empty object.")

    results = [settings.set(k, v) for k, v in updates.items()]
    failed = [r for r in results if not r.get("applied")]
    if failed:
        raise HTTPException(status_code=400, detail=failed[0].get("error", "bad setting"))

    hints = [r["restart_hint"] for r in results if r.get("restart_needed")]
    return {
        "results": results,
        "restart_hint": hints[0] if hints else "Applied live — no restart needed.",
        "effective": settings.effective(),
    }
