# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``research`` seed — the geshe's aid department + the idea-intake flow.

The geshe (the temple's scholar-monk, owner of ``research/METHOD.md``) gains
its support team: four AIDE brains under it, one per step of the corpus
method, plus the canonical **idea-intake work-flow** that turns "an idea" (a
link, a technique, a claim) into a corpus-style draft:

  collector  → capture the idea's core + every source/nested resource to chase
  analyst    → mechanics down to the base
  skeptic    → verify claims against sources; label theirs-vs-ours; name the
               attribution chain and the unverified (the doer never judges —
               verification is its own brain)
  synthesist → write the seven-section corpus document

A structure gate (objective, LLM-free regex árbitro) refuses a synthesis
missing the method's section headers; the ``doc`` output records the draft in
the run record (the ``/idea`` door writes it to ``doc/notes/drafts/`` and
delivers it up the hierarchy). Idempotent, mirrors :mod:`lhassa.seeds.cv`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.flows.model import Flow
from lhassa.flows.store import FlowStore
from lhassa.org.temple import GESHE, GESHE_AIDES
from lhassa.seeds import register_seed
from lhassa.seeds.temple import seed_temple

if TYPE_CHECKING:
    from lhassa.config import Config

DEFAULT_RESEARCH_FLOW_ID = "flow-research-intake"
DEFAULT_ASSESSMENT_FLOW_ID = "flow-idea-assessment"
AIDE_ROLE = "aide"
RESEARCH_DEPARTMENT = "research"
AIDE_SKILLS = ("research-method", "no-ai-slop")

_FLOW_JSON = """{
  "id": "flow-research-intake",
  "name": "Idea intake — down to the base",
  "description": "From an idea (a link, a technique, a claim): capture sources, analyze mechanics, verify claims, synthesize a corpus-style draft per research/METHOD.md.",
  "nodes": [
    {"id": "trigger-1", "kind": "trigger", "config": {"type": "manual"},
     "label": "The idea (text, links, claims)"},
    {"id": "task-1", "kind": "task",
     "config": {"brain": "collector", "max_tokens": 1400,
       "input_mode": "full", "preserve_context": true,
       "prompt": "You receive a PRE-MODEL evidence dossier with immutable source ids, capture status, URLs, hashes and captured text:\\n{input}\\n\\nBuild the capture brief: (1) core idea in one sentence, (2) each distinct claim, (3) the exact source id(s) that could support it, (4) nested resources already captured and important resources still NOT CAPTURED, (5) evidence needed to trust it. Never treat a search snippet as proof. Never invent a source or imply you opened anything absent from the dossier."},
     "label": "Capture sources"},
    {"id": "task-2", "kind": "task",
     "config": {"brain": "analyst", "max_tokens": 1500,
       "input_mode": "full", "preserve_context": true,
       "prompt": "Using the COMPLETE dossier and capture brief below, explain the MECHANICS down to the base: how it works step by step, dependencies, assumptions and breakpoints. Cite captured support inline as [S#]. If the captured bytes do not establish a mechanism, label it UNVERIFIED. No adoption opinion.\\n\\n{input}"},
     "label": "Mechanics down to the base"},
    {"id": "task-3", "kind": "task",
     "config": {"brain": "skeptic", "max_tokens": 1500,
       "input_mode": "full", "preserve_context": true,
       "prompt": "You are the skeptic; the analyst never judges its own work. Audit the COMPLETE dossier, not the analyst's confidence. Produce a table: Claim | Supporting captured fragment + [S#] | SUPPORTED / PARTIAL / UNVERIFIED. A citation is valid only when that source's captured text actually supports the claim. Label every external number AUTHORS' EVIDENCE, name attribution chains and over-attribution, and list what only OUR instruments could verify.\\n\\n{input}"},
     "label": "Verify claims (skeptic)"},
    {"id": "task-4", "kind": "task",
     "config": {"brain": "synthesist", "max_tokens": 3000,
       "input_mode": "full", "preserve_context": true,
       "prompt": "Write a corpus document from the COMPLETE custody dossier and audit below. Use EXACTLY these seven ## sections, in order: The idea in one sentence / Mechanics - down to the base / Evidence the authors present / Sources / Mapping to Lhassa / Not yet verified by us - and the experiment that would / Open questions. Declare sources as '- S#: <URL or capture failure>; sha256=<hash when captured>'. Every evidence bullet must cite [S#] or say UNVERIFIED. Never call external evidence ours. Section 6 MUST explicitly name Baseline, Instrument, Metric, and Stop rule. Calm, plain, no hype or filler.\\n\\n{input}"},
     "label": "Synthesize the corpus draft"},
    {"id": "gate-1", "kind": "gate",
     "config": {"type": "verify",
       "spec": {"type": "research_corpus"}},
     "label": "Method + citation gate"},
    {"id": "output-1", "kind": "output",
     "config": {"type": "doc", "target": "research-draft"},
     "label": "Record the draft"}
  ],
  "edges": [
    {"from_node": "trigger-1", "to_node": "task-1"},
    {"from_node": "task-1", "to_node": "task-2"},
    {"from_node": "task-2", "to_node": "task-3"},
    {"from_node": "task-3", "to_node": "task-4"},
    {"from_node": "task-4", "to_node": "gate-1"},
    {"from_node": "gate-1", "to_node": "output-1"}
  ]
}"""

_ASSESSMENT_FLOW_JSON = """{
  "id": "flow-idea-assessment",
  "name": "Idea assessment — earn the research budget",
  "description": "Theory-first screening before an idea receives applied or rigorous research effort.",
  "nodes": [
    {"id": "trigger-1", "kind": "trigger", "config": {"type": "manual"},
     "label": "Captured idea and source custody"},
    {"id": "task-1", "kind": "task",
     "config": {"brain": "analyst", "max_tokens": 1300,
       "input_mode": "full", "preserve_context": true,
       "prompt": "Screen the idea THEORY-FIRST from this captured dossier. Reconstruct the proposed mechanism, its assumptions and dependency chain. Prefer primary sources. Every external statement must cite an actually captured [S#]; if the dossier cannot support the theory, say UNVERIFIED. Do not recommend adoption and do not score it yet.\\n\\n{input}"},
     "label": "Reconstruct theoretical basis"},
    {"id": "task-2", "kind": "task",
     "config": {"brain": "skeptic", "max_tokens": 2200,
       "input_mode": "full", "preserve_context": true,
       "prompt": "Audit the idea and theory brief, then output ONLY one valid JSON object (no commentary). Copy source_registry ONLY from source ids present in the custody dossier. Shape: {\\\"source_registry\\\":[\\\"S1\\\"],\\\"theory\\\":{\\\"mechanism\\\":\\\"at least 30 chars, cited [S#] or explicitly UNVERIFIED\\\",\\\"assumptions\\\":[\\\"...\\\"],\\\"source_ids\\\":[\\\"S1\\\"]},\\\"criteria\\\":{...},\\\"uncertainties\\\":[\\\"...\\\"]}. criteria MUST contain exactly: strategic_value, architectural_fit, theoretical_grounding, evidence_quality, testability, novelty, knowledge_reuse, feasibility, reversibility. Each value is {\\\"score\\\": integer 0..3, \\\"rationale\\\": \\\"specific reason, at least 12 chars\\\", \\\"source_ids\\\": [captured S ids]}. Score conservatively: 0 absent/contradicted, 1 weak, 2 credible, 3 strong. For theoretical_grounding/evidence_quality/novelty, a positive score needs captured source ids or rationale must say UNVERIFIED. Strategic fit is Lhassa-specific judgment and need not cite. Name uncertainties; never hide missing evidence. Do not choose the investment level—the deterministic gate does that.\\n\\n{input}"},
     "label": "Score the complete rubric"},
    {"id": "gate-1", "kind": "gate",
     "config": {"type": "verify", "spec": {"type": "idea_assessment"}},
     "label": "Rubric + theory gate"},
    {"id": "output-1", "kind": "output",
     "config": {"type": "doc", "target": "idea-assessment"},
     "label": "Record assessment"}
  ],
  "edges": [
    {"from_node": "trigger-1", "to_node": "task-1"},
    {"from_node": "task-1", "to_node": "task-2"},
    {"from_node": "task-2", "to_node": "gate-1"},
    {"from_node": "gate-1", "to_node": "output-1"}
  ]
}"""


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def _canonical_flow(flow_id: str, source: str = _FLOW_JSON) -> Flow:
    data = json.loads(source)
    data["id"] = flow_id
    return Flow.from_dict(data)


def seed_research(config: "Optional[Config]" = None) -> dict:
    """Create the geshe's aides + the idea-intake flow. Idempotent."""
    cfg = _resolve_config(config)
    flow_id = getattr(cfg, "research_flow_id", None) or DEFAULT_RESEARCH_FLOW_ID
    assessment_flow_id = (
        getattr(cfg, "research_assessment_flow_id", None)
        or DEFAULT_ASSESSMENT_FLOW_ID
    )
    summary: dict = {
        "aides_created": [], "aides_existing": [], "aides_updated": [], "flow_id": flow_id,
        "flow_created": False, "flow_updated": False,
        "assessment_flow_id": assessment_flow_id,
        "assessment_flow_created": False, "assessment_flow_updated": False,
        "errors": [],
    }

    # The aides hang UNDER the geshe — ensure the temple (and the geshe) first.
    try:
        temple = seed_temple(cfg)
        if temple.get("errors"):
            summary["errors"].extend(temple["errors"])
    except Exception as exc:
        summary["errors"].append(f"temple: {type(exc).__name__}: {exc}")
        return summary

    from lhassa.org import management
    from lhassa.org.topology import load_topology

    for aide_id in GESHE_AIDES:
        try:
            topology = load_topology(cfg)
            if topology.has(aide_id):
                node = topology.node(aide_id)
                if node.parent != GESHE:
                    summary["errors"].append(
                        f"{aide_id}: existing brain belongs under {node.parent!r}, "
                        f"not {GESHE!r}; refusing to hijack it"
                    )
                    continue
                merged_skills = tuple(dict.fromkeys([*(node.skills or ()), *AIDE_SKILLS]))
                if merged_skills != tuple(node.skills or ()):
                    management.update_brain(aide_id, skills=merged_skills, config=cfg)
                    summary["aides_updated"].append(aide_id)
                summary["aides_existing"].append(aide_id)
                continue
            management.add_brain(
                aide_id,
                role=AIDE_ROLE,
                parent=GESHE,
                department=RESEARCH_DEPARTMENT,
                skills=AIDE_SKILLS,
                config=cfg,
            )
            summary["aides_created"].append(aide_id)
        except Exception as exc:
            logger.warning("seed[research]: aide {} failed: {}", aide_id, exc)
            summary["errors"].append(f"{aide_id}: {type(exc).__name__}: {exc}")

    try:
        store = FlowStore(cfg)
        canonical = _canonical_flow(flow_id)
        existing = store.get(flow_id)
        if existing is None:
            store.save(canonical)
            summary["flow_created"] = True
        elif existing.to_dict() != canonical.to_dict():
            store.save(canonical)
            summary["flow_updated"] = True
    except Exception as exc:
        logger.warning("seed[research]: flow failed: {}", exc)
        summary["errors"].append(f"flow: {type(exc).__name__}: {exc}")

    try:
        store = FlowStore(cfg)
        canonical = _canonical_flow(assessment_flow_id, _ASSESSMENT_FLOW_JSON)
        existing = store.get(assessment_flow_id)
        if existing is None:
            store.save(canonical)
            summary["assessment_flow_created"] = True
        elif existing.to_dict() != canonical.to_dict():
            store.save(canonical)
            summary["assessment_flow_updated"] = True
    except Exception as exc:
        logger.warning("seed[research]: assessment flow failed: {}", exc)
        summary["errors"].append(
            f"assessment-flow: {type(exc).__name__}: {exc}"
        )

    return summary


@register_seed(
    "research",
    "Create the geshe's research aid department (collector/analyst/skeptic/"
    "synthesist under the scholar-monk) and the idea-intake work-flow that "
    "turns an idea into a corpus-style draft per research/METHOD.md.",
)
def apply(config: "Optional[Config]" = None) -> dict:
    """The ``research`` seed entry point — see :func:`seed_research`."""
    return seed_research(config)
