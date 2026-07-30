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
AIDE_ROLE = "aide"
RESEARCH_DEPARTMENT = "research"

_FLOW_JSON = """{
  "id": "flow-research-intake",
  "name": "Idea intake — down to the base",
  "description": "From an idea (a link, a technique, a claim): capture sources, analyze mechanics, verify claims, synthesize a corpus-style draft per research/METHOD.md.",
  "nodes": [
    {"id": "trigger-1", "kind": "trigger", "config": {"type": "manual"},
     "label": "The idea (text, links, claims)"},
    {"id": "task-1", "kind": "task",
     "config": {"brain": "collector", "max_tokens": 1200,
       "prompt": "From this idea/material: {input}\\n\\nExtract: (1) the core idea in one sentence, (2) every claim made, (3) every source/URL named or implied that should be chased (nested resources included), (4) what evidence would be needed to trust each claim. Return a structured capture brief."},
     "label": "Capture sources"},
    {"id": "task-2", "kind": "task",
     "config": {"brain": "analyst", "max_tokens": 1500,
       "prompt": "Given this capture brief: {input}\\n\\nExplain the MECHANICS down to the base: how the technique/idea actually works step by step, what it depends on, where it breaks. No adoption opinions - mechanics only."},
     "label": "Mechanics down to the base"},
    {"id": "task-3", "kind": "task",
     "config": {"brain": "skeptic", "max_tokens": 1500,
       "prompt": "You are the verifier - the doer never judges, you do. Given the capture+analysis: {input}\\n\\nFor each claim: supported by which named source, or UNVERIFIED. Label every number as the AUTHORS' evidence (never ours). Name the attribution chain and any over-attribution. List what only OUR instruments could verify."},
     "label": "Verify claims (skeptic)"},
    {"id": "task-4", "kind": "task",
     "config": {"brain": "synthesist", "max_tokens": 3000,
       "prompt": "Write a corpus document per research/METHOD.md from this verified material: {input}\\n\\nEXACTLY these seven ## sections: The idea in one sentence / Mechanics - down to the base / Evidence the authors present / Sources / Mapping to Lhassa / Not yet verified by us - and the experiment that would / Open questions. Calm, no slop, claim->cited evidence; unknown mappings say so honestly."},
     "label": "Synthesize the corpus draft"},
    {"id": "gate-1", "kind": "gate",
     "config": {"type": "verify",
       "spec": {"type": "regex", "pattern": "## Open questions"}},
     "label": "Method-structure gate"},
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


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def _canonical_flow(flow_id: str) -> Flow:
    data = json.loads(_FLOW_JSON)
    data["id"] = flow_id
    return Flow.from_dict(data)


def seed_research(config: "Optional[Config]" = None) -> dict:
    """Create the geshe's aides + the idea-intake flow. Idempotent."""
    cfg = _resolve_config(config)
    flow_id = getattr(cfg, "research_flow_id", None) or DEFAULT_RESEARCH_FLOW_ID
    summary: dict = {
        "aides_created": [], "aides_existing": [], "flow_id": flow_id,
        "flow_created": False, "errors": [],
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
            if load_topology(cfg).has(aide_id):
                summary["aides_existing"].append(aide_id)
                continue
            management.add_brain(
                aide_id,
                role=AIDE_ROLE,
                parent=GESHE,
                department=RESEARCH_DEPARTMENT,
                config=cfg,
            )
            summary["aides_created"].append(aide_id)
        except Exception as exc:
            logger.warning("seed[research]: aide {} failed: {}", aide_id, exc)
            summary["errors"].append(f"{aide_id}: {type(exc).__name__}: {exc}")

    try:
        store = FlowStore(cfg)
        if store.get(flow_id) is None:
            store.save(_canonical_flow(flow_id))
            summary["flow_created"] = True
    except Exception as exc:
        logger.warning("seed[research]: flow failed: {}", exc)
        summary["errors"].append(f"flow: {type(exc).__name__}: {exc}")

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
