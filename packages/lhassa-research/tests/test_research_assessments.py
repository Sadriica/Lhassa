from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lhassa.research.surfaces import api as research_routes
from lhassa.config import Config
from lhassa.core import db
from lhassa.research import assessments as ideas


def document(score: int = 3) -> dict:
    return {
        "source_registry": ["S1"],
        "theory": {
            "mechanism": "The captured mechanism composes bounded stages [S1].",
            "assumptions": ["The workload resembles the measured workload."],
            "source_ids": ["S1"],
        },
        "criteria": {
            name: {
                "score": score,
                "rationale": f"Specific rationale for {name} supported here.",
                "source_ids": (
                    ["S1"] if name in ideas.EVIDENCE_BOUND else []
                ),
            }
            for name in ideas.CRITERIA_WEIGHTS
        },
        "uncertainties": ["External validity remains unknown."],
    }


@pytest.mark.parametrize(
    ("score", "level", "decision"),
    [
        (3, "rigorous", "experiment"),
        (2, "applied", "research"),
        (1, "exploration", "watch"),
    ],
)
def test_rubric_deterministically_assigns_investment(score, level, decision):
    result = ideas.score_document(document(score), declared_source_ids={"S1"})
    assert result["investment_level"] == level
    assert result["decision"] == decision
    assert result["weighted_score"] == score * 14
    assert result["max_weighted_score"] == 42


def test_hard_reject_archives_even_when_other_scores_are_high():
    value = document(3)
    value["criteria"]["strategic_value"]["score"] = 0
    result = ideas.score_document(value, declared_source_ids={"S1"})
    assert result["investment_level"] == "none"
    assert result["decision"] == "archive"


def test_rubric_rejects_invented_source_ids():
    value = document(3)
    value["criteria"]["evidence_quality"]["source_ids"] = ["S999"]
    with pytest.raises(ValueError, match="undeclared sources: S999"):
        ideas.score_document(value, declared_source_ids={"S1"})


@pytest.mark.asyncio
async def test_every_projection_transition_has_an_append_only_event():
    await db.init_db()
    row = await ideas.create(
        "inst_ideas",
        idea="bounded research loops",
        evidence_digest="a" * 64,
        evidence_path="/tmp/evidence.json",
        evidence_summary={"source_count": 1},
    )
    await ideas.start_screening("inst_ideas", row["id"])
    result = ideas.score_document(document(2), declared_source_ids={"S1"})
    await ideas.record_assessment(
        "inst_ideas",
        row["id"],
        result,
        assessment_path="/tmp/assessment.json",
        run_id="run_assess",
    )
    final = await ideas.record_decision("inst_ideas", row["id"])

    assert final["status"] == "approved_for_research"
    assert final["investment_level"] == "applied"
    events = await db.list_idea_assessment_events("inst_ideas", row["id"])
    assert [event["stage"] for event in events] == [
        "evidence_captured",
        "theory_screen_started",
        "idea_scored",
        "investment_decided",
    ]
    assert events[0]["from_status"] is None
    assert events[-1]["to_status"] == "approved_for_research"
    assert events[-1]["payload"]["decision"] == "research"


@pytest.mark.asyncio
async def test_creation_is_idempotent_by_evidence_digest():
    await db.init_db()
    first = await ideas.create(
        "inst_ideas",
        idea="same idea",
        evidence_digest="b" * 64,
        evidence_path="/tmp/a.json",
    )
    second = await ideas.create(
        "inst_ideas",
        idea="same idea",
        evidence_digest="b" * 64,
        evidence_path="/tmp/a.json",
    )
    assert second["id"] == first["id"]
    events = await db.list_idea_assessment_events("inst_ideas", first["id"])
    assert len(events) == 1


def test_research_api_returns_projection_with_complete_event_log():
    async def seed():
        await db.init_db()
        row = await ideas.create(
            Config.instance_id,
            idea="API-visible idea",
            evidence_digest="c" * 64,
            evidence_path="/tmp/evidence.json",
        )
        await ideas.start_screening(Config.instance_id, row["id"])
        return row["id"]

    assessment_id = asyncio.run(seed())
    app = FastAPI()
    app.include_router(research_routes.router)
    client = TestClient(app)

    listing = client.get("/api/research/assessments")
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == assessment_id
    detail = client.get(f"/api/research/assessments/{assessment_id}")
    assert detail.status_code == 200
    assert [event["stage"] for event in detail.json()["events"]] == [
        "evidence_captured",
        "theory_screen_started",
    ]
