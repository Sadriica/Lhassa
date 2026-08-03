# SPDX-License-Identifier: AGPL-3.0-or-later
"""Durable ``/idea`` intake: evidence custody → research flow → versioned draft.

The Telegram surface mints this action before acknowledging the request.  That
places the owner's instruction in the signed tasks table, so a process restart
cannot erase it; ``run_task_now`` is only an early heartbeat.  The action then:

1. captures a deterministic MUNDO-trust source bundle before any model call;
2. persists that custody JSON under a content-addressed filename;
3. starts the canonical research flow durably with the bundle digest as its
   idempotency key;
4. writes the gated synthesis beside the evidence without overwriting history;
5. delivers the result upward from the geshe.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import suppress

from loguru import logger


def _flow_output(record: dict | None) -> str:
    """Return a flow's final document, or its last task result as fallback."""
    steps = (record or {}).get("steps", []) or []
    for step in steps:
        if step.get("kind") != "output":
            continue
        payload = step.get("payload") or step.get("output") or ""
        if isinstance(payload, dict):
            payload = payload.get("summary") or payload.get("output") or ""
        if str(payload).strip():
            return str(payload).strip()
    for step in reversed(steps):
        if step.get("kind") != "task":
            continue
        output = step.get("result") or step.get("output")
        if isinstance(output, dict):
            output = output.get("summary") or output.get("output") or ""
        if str(output or "").strip():
            return str(output).strip()
    return ""


async def research_intake(params: dict | None = None) -> str:
    params = params if isinstance(params, dict) else {}
    idea = str(params.get("idea") or "").strip()
    if not idea:
        return "research_intake: no idea"

    assessment_id = ""
    try:
        from lhassa.actionpack._research_note import _DRAFT_HEADER, _drafts_dir
        from lhassa.core import artifacts, db
        from lhassa.core import idea_assessments as assessments
        from lhassa.agents.research_evidence import (
            build_evidence_bundle,
            bundle_for_model,
        )
        from lhassa.config import Config
        from lhassa.flows.store import FlowStore
        from lhassa.org.delegate import delegate_flow
        from lhassa.org.deliver import deliver

        cfg = params.get("config") or Config()
        flow_id = getattr(cfg, "research_flow_id", None) or "flow-research-intake"
        assessment_flow_id = (
            getattr(cfg, "research_assessment_flow_id", None)
            or "flow-idea-assessment"
        )
        flow = FlowStore(cfg).get(flow_id)
        assessment_flow = FlowStore(cfg).get(assessment_flow_id)
        if flow is None or assessment_flow is None:
            missing = flow_id if flow is None else assessment_flow_id
            message = (
                "I can't investigate that yet — the research flow isn't seeded "
                "(run: lhassa seed apply research)."
            )
            await deliver(
                message,
                kind="action-result",
                origin_department="geshe",
                config=cfg,
            )
            return f"research_intake: unavailable ({missing} not seeded)"

        bundle = await build_evidence_bundle(idea)
        digest = str(bundle.get("bundle_sha256") or "")
        if not digest:
            digest = hashlib.sha256(
                json.dumps(bundle, sort_keys=True, default=str).encode()
            ).hexdigest()
        slug = re.sub(r"[^a-z0-9]+", "-", idea.lower()).strip("-")[:48] or "idea"
        version = digest[:10]
        drafts = _drafts_dir(cfg)
        drafts.mkdir(parents=True, exist_ok=True)
        evidence_path = drafts / f"idea-{slug}-{version}.evidence.json"
        evidence_path.write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        await db.init_db()
        assessment = await assessments.create(
            instance_id,
            idea=idea,
            evidence_digest=digest,
            evidence_path=str(evidence_path),
            evidence_summary={
                "source_count": len(bundle.get("sources") or []),
                "captured": sum(
                    1
                    for source in (bundle.get("sources") or [])
                    if source.get("status") == "captured"
                ),
            },
        )
        assessment_id = assessment["id"]
        status = assessment["status"]
        if status == "researched":
            return (
                f"research_intake: assessment={assessment_id}; "
                f"draft={assessment.get('draft_path')}; idempotent"
            )
        if status == "closed":
            return (
                f"research_intake: assessment={assessment_id}; "
                f"decision={assessment.get('decision')}; idempotent"
            )
        if status == "failed":
            return (
                f"research_intake: assessment={assessment_id}; "
                "status=failed; see assessment event log"
            )

        if status == "captured":
            assessment = await assessments.start_screening(
                instance_id, assessment_id
            )
            status = assessment["status"]

        if status == "screening":
            assessment_record = await delegate_flow(
                assessment_flow,
                inputs={
                    "summary": idea,
                    "evidence_bundle": bundle_for_model(bundle),
                    "evidence_path": str(evidence_path),
                },
                config=cfg,
                durable=True,
                idempotency_key=f"idea-assessment:{digest}",
            )
            assessment_text = _flow_output(assessment_record)
            if not assessment_text:
                await assessments.fail(
                    instance_id,
                    assessment_id,
                    stage="assessment_gate_failed",
                    error="assessment flow produced no gated document",
                )
                await deliver(
                    f"I screened the idea, but its theory/rubric gate did not pass. "
                    f"Assessment: {assessment_id}; evidence: {evidence_path}",
                    kind="action-result",
                    origin_department="geshe",
                    config=cfg,
                )
                return (
                    f"research_intake: assessment gate failed; "
                    f"assessment={assessment_id}"
                )
            try:
                document = assessments.parse_document(assessment_text)
                declared = {
                    str(source.get("id"))
                    for source in (bundle.get("sources") or [])
                    if source.get("id")
                }
                scored = assessments.score_document(
                    document, declared_source_ids=declared
                )
            except ValueError as exc:
                await assessments.fail(
                    instance_id,
                    assessment_id,
                    stage="assessment_validation_failed",
                    error=str(exc),
                )
                await deliver(
                    f"I screened the idea, but the evidence-bound rubric was "
                    f"invalid: {str(exc)[:400]}. Assessment: {assessment_id}",
                    kind="action-result",
                    origin_department="geshe",
                    config=cfg,
                )
                return (
                    f"research_intake: assessment validation failed; "
                    f"assessment={assessment_id}"
                )
            assessment_path = drafts / f"idea-{slug}-{version}.assessment.json"
            assessment_path.write_text(
                json.dumps(
                    {
                        "assessment_id": assessment_id,
                        "idea": idea,
                        "evidence_sha256": digest,
                        **scored,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            assessment_run_id = str((assessment_record or {}).get("id") or "")
            await artifacts.register(
                instance_id,
                kind="idea_assessment",
                paths=[assessment_path, evidence_path],
                metadata={
                    "assessment_id": assessment_id,
                    "decision": scored["decision"],
                    "investment_level": scored["investment_level"],
                    "weighted_score": scored["weighted_score"],
                    "evidence_digest": digest,
                    "run_id": assessment_run_id,
                },
                origin_department="research",
            )
            assessment = await assessments.record_assessment(
                instance_id,
                assessment_id,
                scored,
                assessment_path=str(assessment_path),
                run_id=assessment_run_id,
            )
            status = assessment["status"]

        if status == "assessed":
            assessment = await assessments.record_decision(
                instance_id, assessment_id
            )
            status = assessment["status"]

        if status == "closed":
            decision = assessment.get("decision")
            level = assessment.get("investment_level")
            await deliver(
                f"Idea screened before deep research: {decision} "
                f"(investment: {level}, score "
                f"{assessment.get('weighted_score')}/42). No full corpus was "
                f"commissioned. Assessment: {assessment_id}; "
                f"artifact: {assessment.get('assessment_path')}",
                kind="action-result",
                origin_department="geshe",
                config=cfg,
            )
            return (
                f"research_intake: assessment={assessment_id}; "
                f"decision={decision}; investment={level}"
            )

        if status == "approved_for_research":
            assessment = await assessments.start_research(
                instance_id, assessment_id
            )
            status = assessment["status"]

        if status != "researching":
            raise RuntimeError(f"unexpected assessment state {status!r}")
        record = await delegate_flow(
            flow,
            inputs={
                "summary": idea,
                "evidence_bundle": bundle_for_model(bundle),
                "evidence_path": str(evidence_path),
                "assessment_id": assessment_id,
                "investment_level": assessment.get("investment_level"),
                "assessment_path": assessment.get("assessment_path"),
            },
            config=cfg,
            durable=True,
            idempotency_key=f"idea:{digest}",
        )

        draft_text = _flow_output(record)
        if not draft_text:
            last = _flow_output(record)
            await assessments.fail(
                instance_id,
                assessment_id,
                stage="research_gate_failed",
                error=last[:1000] or "full research produced no gated draft",
            )
            message = (
                "The idea intake didn't pass its research gate — the team said:\n\n"
                + (last[:500] or "(no output recorded)")
                + f"\n\nEvidence custody: {evidence_path}"
            )
            await deliver(
                message,
                kind="action-result",
                origin_department="geshe",
                config=cfg,
            )
            return f"research_intake: gate did not pass; evidence={evidence_path}"

        path = drafts / f"idea-{slug}-{version}.md"
        run_id = str((record or {}).get("id") or "")
        path.write_text(
            f"{_DRAFT_HEADER}\n"
            f"<!-- idea intake via /idea | flow: {flow_id} | run: {run_id} | "
            f"evidence: {evidence_path.name} | sha256: {digest} -->\n\n"
            f"{draft_text}\n",
            encoding="utf-8",
        )
        await assessments.complete_research(
            instance_id,
            assessment_id,
            run_id=run_id,
            draft_path=str(path),
        )
        await artifacts.register(
            instance_id,
            kind="research_corpus",
            paths=[path, evidence_path, assessment.get("assessment_path")],
            metadata={
                "assessment_id": assessment_id,
                "decision": assessment.get("decision"),
                "investment_level": assessment.get("investment_level"),
                "evidence_digest": digest,
                "run_id": run_id,
            },
            origin_department="research",
        )
        await deliver(
            f"Idea investigated — versioned corpus draft at {path}; evidence "
            f"custody at {evidence_path}; assessment {assessment_id} earned "
            f"{assessment.get('investment_level')} investment "
            f"({assessment.get('decision')}). Review before publishing.",
            kind="action-result",
            origin_department="geshe",
            config=cfg,
        )
        return (
            f"research_intake: assessment={assessment_id}; draft={path}; "
            f"evidence={evidence_path}; run={run_id}"
        )
    except Exception as exc:
        if assessment_id:
            with suppress(Exception):
                from lhassa.config import Config
                from lhassa.core import idea_assessments as assessments

                cfg = params.get("config") or Config()
                instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
                await assessments.fail(
                    instance_id,
                    assessment_id,
                    stage="intake_failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
        # Let the signed task runner keep the row pending for a later heartbeat.
        logger.warning(f"research_intake failed for {idea!r}: {exc}")
        raise


__all__ = ["research_intake"]
