"""Read/run surface for deterministic conformance and attestations."""

from fastapi import APIRouter, Query

from lhassa.config import Config
from lhassa.core import db
from lhassa.core.conformance import MANIFEST_VERSION, attestation
from lhassa.org.integrity import CONTRACTS, run_integrity

router = APIRouter(prefix="/api/integrity", tags=["integrity"])


@router.get("")
async def integrity_status() -> dict:
    await db.init_db()
    return {
        "manifest_version": MANIFEST_VERSION,
        "attestation": await attestation(Config.instance_id),
        "state": await db.get_conformance_state(Config.instance_id),
        "contracts": [
            {
                "id": contract.id,
                "version": contract.version,
                "severity": contract.severity,
                "description": contract.description,
            }
            for contract in CONTRACTS
        ],
    }


@router.post("/check")
async def integrity_check() -> dict:
    return await run_integrity(trigger="api")


@router.get("/findings")
async def integrity_findings(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    await db.init_db()
    return await db.list_conformance_findings(Config.instance_id, limit=limit)
