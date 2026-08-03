# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only research-funnel projections and their immutable event logs."""

from fastapi import APIRouter, HTTPException, Query

from lhassa.config import Config
from lhassa.core import db

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("/assessments")
async def list_assessments(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    await db.init_db()
    return await db.list_idea_assessments(
        Config.instance_id, status=status, limit=limit
    )


@router.get("/assessments/{assessment_id}")
async def get_assessment(assessment_id: str) -> dict:
    await db.init_db()
    row = await db.get_idea_assessment(Config.instance_id, assessment_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"idea assessment {assessment_id!r} not found",
        )
    events = await db.list_idea_assessment_events(
        Config.instance_id, assessment_id
    )
    return {**row, "events": events}
