# SPDX-License-Identifier: AGPL-3.0-or-later
"""Durable, evidence-bound triage before expensive research.

The model may propose criterion scores, but it does not decide the budget.
This module validates the structured assessment, recomputes the weighted score,
and deterministically maps it to an investment level and disposition.

Every lifecycle method delegates to the DB's atomic
``event append + projection update`` seam. The current row is convenient; the
append-only event stream is the audit source of truth.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from lhassa.core import db

CRITERIA_WEIGHTS: dict[str, int] = {
    "strategic_value": 2,
    "architectural_fit": 2,
    "theoretical_grounding": 2,
    "evidence_quality": 2,
    "testability": 2,
    "novelty": 1,
    "knowledge_reuse": 1,
    "feasibility": 1,
    "reversibility": 1,
}
EVIDENCE_BOUND = {
    "theoretical_grounding",
    "evidence_quality",
    "novelty",
}
INVESTMENT_DECISION = {
    "exploration": "watch",
    "applied": "research",
    "rigorous": "experiment",
}


def parse_document(text: str) -> dict:
    """Parse one assessment JSON object, tolerating a single Markdown fence."""
    raw = str(text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"assessment is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("assessment must be a JSON object")
    return value


def _source_ids(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).startswith("S")]


def score_document(document: dict, *, declared_source_ids: set[str]) -> dict:
    """Validate a model-produced assessment and determine its budget.

    Raises ``ValueError`` with all objective contract failures. Scores are
    integers 0..3 and remain visible alongside their rationales and citations;
    the weighted total is a routing aid, never presented as scientific truth.
    """
    if not isinstance(document, dict):
        raise ValueError("assessment must be a JSON object")
    theory = document.get("theory")
    criteria = document.get("criteria")
    uncertainties = document.get("uncertainties")
    failures: list[str] = []
    if not isinstance(theory, dict):
        failures.append("theory must be an object")
        theory = {}
    mechanism = str(theory.get("mechanism") or "").strip()
    if len(mechanism) < 30:
        failures.append("theory.mechanism is too short")
    assumptions = theory.get("assumptions")
    if not isinstance(assumptions, list) or not assumptions:
        failures.append("theory.assumptions must be a non-empty list")
    theory_sources = _source_ids(theory.get("source_ids"))
    unknown_theory = set(theory_sources) - declared_source_ids
    if unknown_theory:
        failures.append(
            "theory cites undeclared sources: " + ", ".join(sorted(unknown_theory))
        )
    if not theory_sources and "unverified" not in mechanism.lower():
        failures.append("theory needs source_ids or an UNVERIFIED mechanism")

    if not isinstance(criteria, dict):
        failures.append("criteria must be an object")
        criteria = {}
    missing = set(CRITERIA_WEIGHTS) - set(criteria)
    extra = set(criteria) - set(CRITERIA_WEIGHTS)
    if missing:
        failures.append("missing criteria: " + ", ".join(sorted(missing)))
    if extra:
        failures.append("unknown criteria: " + ", ".join(sorted(extra)))

    normalized: dict[str, dict] = {}
    for name in CRITERIA_WEIGHTS:
        item = criteria.get(name)
        if not isinstance(item, dict):
            failures.append(f"{name} must be an object")
            continue
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 3:
            failures.append(f"{name}.score must be an integer from 0 to 3")
            continue
        rationale = str(item.get("rationale") or "").strip()
        if len(rationale) < 12:
            failures.append(f"{name}.rationale is too short")
        sources = _source_ids(item.get("source_ids"))
        unknown = set(sources) - declared_source_ids
        if unknown:
            failures.append(
                f"{name} cites undeclared sources: " + ", ".join(sorted(unknown))
            )
        if (
            name in EVIDENCE_BOUND
            and score > 0
            and not sources
            and "unverified" not in rationale.lower()
        ):
            failures.append(f"{name} needs source_ids or an UNVERIFIED rationale")
        normalized[name] = {
            "score": score,
            "rationale": rationale,
            "source_ids": sources,
        }
    if not isinstance(uncertainties, list) or not uncertainties:
        failures.append("uncertainties must be a non-empty list")
    if failures:
        raise ValueError("; ".join(failures))

    weighted = sum(
        normalized[name]["score"] * weight
        for name, weight in CRITERIA_WEIGHTS.items()
    )
    scores = {name: item["score"] for name, item in normalized.items()}
    hard_reject = (
        scores["strategic_value"] == 0
        or scores["architectural_fit"] == 0
        or (
            scores["theoretical_grounding"] == 0
            and scores["evidence_quality"] == 0
        )
    )
    if hard_reject:
        investment, decision = "none", "archive"
    elif (
        weighted >= 32
        and min(
            scores["strategic_value"],
            scores["architectural_fit"],
            scores["theoretical_grounding"],
            scores["evidence_quality"],
            scores["testability"],
        )
        >= 2
        and scores["feasibility"] >= 1
    ):
        investment = "rigorous"
        decision = INVESTMENT_DECISION[investment]
    elif (
        weighted >= 22
        and scores["strategic_value"] >= 2
        and scores["architectural_fit"] >= 2
        and scores["theoretical_grounding"] >= 1
        and scores["testability"] >= 1
    ):
        investment = "applied"
        decision = INVESTMENT_DECISION[investment]
    else:
        investment = "exploration"
        decision = INVESTMENT_DECISION[investment]
    return {
        "theory": {
            "mechanism": mechanism,
            "assumptions": [str(item) for item in assumptions],
            "source_ids": theory_sources,
        },
        "criteria": normalized,
        "uncertainties": [str(item) for item in uncertainties],
        "weighted_score": weighted,
        "max_weighted_score": sum(CRITERIA_WEIGHTS.values()) * 3,
        "investment_level": investment,
        "decision": decision,
    }


async def create(
    instance_id: str,
    *,
    idea: str,
    evidence_digest: str,
    evidence_path: str,
    evidence_summary: Optional[dict] = None,
) -> dict:
    return await db.create_idea_assessment(
        instance_id,
        idea=idea,
        evidence_digest=evidence_digest,
        evidence_path=evidence_path,
        payload={"evidence": evidence_summary or {}},
    )


async def start_screening(instance_id: str, assessment_id: str) -> dict:
    return await db.transition_idea_assessment(
        instance_id,
        assessment_id,
        stage="theory_screen_started",
        to_status="screening",
        expected_status="captured",
    )


async def record_assessment(
    instance_id: str,
    assessment_id: str,
    result: dict,
    *,
    assessment_path: str,
    run_id: str,
) -> dict:
    return await db.transition_idea_assessment(
        instance_id,
        assessment_id,
        stage="idea_scored",
        to_status="assessed",
        expected_status="screening",
        updates={
            "investment_level": result["investment_level"],
            "decision": result["decision"],
            "weighted_score": result["weighted_score"],
            "criteria": result["criteria"],
            "theory": result["theory"],
            "uncertainties": result["uncertainties"],
            "assessment_path": assessment_path,
            "assessment_run_id": run_id,
        },
        payload={
            "max_weighted_score": result["max_weighted_score"],
            "rubric_version": 1,
        },
    )


async def record_decision(instance_id: str, assessment_id: str) -> dict:
    row = await db.get_idea_assessment(instance_id, assessment_id)
    if row is None:
        raise KeyError(assessment_id)
    decision = str(row.get("decision") or "")
    terminal = decision in {"archive", "watch"}
    return await db.transition_idea_assessment(
        instance_id,
        assessment_id,
        stage="investment_decided",
        to_status="closed" if terminal else "approved_for_research",
        expected_status="assessed",
        payload={
            "decision": decision,
            "investment_level": row.get("investment_level"),
            "weighted_score": row.get("weighted_score"),
        },
    )


async def start_research(
    instance_id: str, assessment_id: str, *, run_id: str = ""
) -> dict:
    return await db.transition_idea_assessment(
        instance_id,
        assessment_id,
        stage="full_research_started",
        to_status="researching",
        expected_status="approved_for_research",
        updates={"research_run_id": run_id} if run_id else {},
    )


async def complete_research(
    instance_id: str,
    assessment_id: str,
    *,
    run_id: str,
    draft_path: str,
) -> dict:
    return await db.transition_idea_assessment(
        instance_id,
        assessment_id,
        stage="full_research_completed",
        to_status="researched",
        expected_status="researching",
        updates={"research_run_id": run_id, "draft_path": draft_path},
    )


async def fail(
    instance_id: str,
    assessment_id: str,
    *,
    stage: str,
    error: str,
) -> dict:
    current = await db.get_idea_assessment(instance_id, assessment_id)
    if current is None:
        raise KeyError(assessment_id)
    if current["status"] in {"closed", "researched", "failed"}:
        return current
    return await db.transition_idea_assessment(
        instance_id,
        assessment_id,
        stage=stage,
        to_status="failed",
        payload={"error": error[:2000]},
    )


async def log_downstream(
    instance_id: str,
    assessment_id: str,
    *,
    stage: str,
    payload: dict,
) -> dict:
    """Append a product-adoption stage without changing research disposition."""
    current = await db.get_idea_assessment(instance_id, assessment_id)
    if current is None:
        raise KeyError(assessment_id)
    return await db.transition_idea_assessment(
        instance_id,
        assessment_id,
        stage=stage,
        to_status=current["status"],
        expected_status=current["status"],
        payload=payload,
    )
