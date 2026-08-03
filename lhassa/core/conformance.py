# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inner conformance contract: manifest identity and attestation validation."""

from __future__ import annotations

from datetime import datetime, timezone

from lhassa.core import db

MANIFEST_VERSION = 1


async def attestation(instance_id: str) -> dict:
    """Return whether the latest conformance run is fresh and entirely green."""
    await db.init_db()
    row = await db.latest_conformance_run(instance_id)
    if row is None:
        return {"valid": False, "reason": "no conformance run exists"}
    if row.get("manifest_version") != MANIFEST_VERSION:
        return {
            "valid": False,
            "reason": "conformance manifest is stale",
            "run": row,
        }
    if row.get("status") != "pass":
        return {
            "valid": False,
            "reason": f"latest conformance status is {row.get('status')}",
            "run": row,
        }
    try:
        expires = datetime.fromisoformat(
            str(row.get("attestation_expires_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return {
            "valid": False,
            "reason": "attestation has no valid expiry",
            "run": row,
        }
    if expires <= datetime.now(timezone.utc):
        return {"valid": False, "reason": "attestation expired", "run": row}
    return {"valid": True, "reason": "fresh green attestation", "run": row}
