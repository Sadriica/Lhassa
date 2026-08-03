# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work-flow TEMPLATES — a gallery of generic, instructive flow presets.

The flows counterpart of :mod:`lhassa.org.templates`: a named catalog of
:class:`~lhassa.flows.model.Flow` builders anyone can apply in one move. Where the
org templates recreate a working STRUCTURE (brains), a flow template recreates a
working PROCESS (a validated DAG through the engine's node kinds).

Design (mirrors ``org/templates.py``'s shape):

  - Each entry is ``{"id", "name", "description", "teaches", "builder"}`` — ``builder``
    is a zero-arg callable returning a VALIDATED :class:`Flow` for a given flow id.
  - Every template routes its ``task`` nodes by ``skill`` (never a pinned personal
    ``brain`` id) so it stays a portable, org-topology-agnostic preset — see
    ``doc/boundaries.md``: the repo carries generic, applyable EXAMPLES; the owner's
    tailored flows live in ``~/.lhassa/flows.json`` and never enter git.
  - :func:`apply_template` instantiates a template into a :class:`FlowStore` under a
    FRESH id, never clobbering an existing flow: a taken id is suffixed
    (``"<id>-2"``, ``"<id>-3"``, …) — the flows analogue of the org templates'
    skip-on-collision idempotency (there a *brain* id collision is skipped; here a
    *flow* is a fresh instance each time, so it is suffixed instead of skipped).

This is a SHELL module (composes the flows model/store); it adds no new core.
"""

from __future__ import annotations

from typing import Callable, Optional, TYPE_CHECKING

from lhassa.flows.model import Flow, FlowEdge, FlowNode, NodeKind
from lhassa.flows.store import FlowStore

if TYPE_CHECKING:
    from lhassa.config import Config


class FlowTemplateError(ValueError):
    """Raised on an unknown flow-template id. A :class:`ValueError` so the API/CLI
    map it to a 4xx / exit-1 with one ``except ValueError``."""


# ── the catalog ──────────────────────────────────────────────────────────────────


def _research_brief(flow_id: str = "research-brief") -> Flow:
    """trigger → task(research) → gate(verify) → output(notify).

    Teaches the smallest verified pipeline: answer a question, then have an
    OBJECTIVE, LLM-free gate check the answer actually cites something before it
    reaches the owner.
    """
    return Flow(
        id=flow_id,
        name="Research Brief",
        description=(
            "Answer a question with checked sources: research → an objective "
            "regex gate (the answer must cite a source) → notify. Teaches the "
            "engine's simplest VERIFIED pipeline (task → gate:verify → output)."
        ),
        nodes=[
            FlowNode("trigger", NodeKind.TRIGGER, {"type": "manual"}, "Question in"),
            FlowNode(
                "research",
                NodeKind.TASK,
                {
                    "skill": "research",
                    "prompt": (
                        "Answer the question below with checked sources. Cite each "
                        "claim inline as (source: ...).\n\n{input}"
                    ),
                },
                "Research the question",
            ),
            FlowNode(
                "checked",
                NodeKind.GATE,
                {
                    "type": "verify",
                    "spec": {"type": "regex", "pattern": r"\(source:"},
                },
                "Sources cited?",
            ),
            FlowNode("brief", NodeKind.OUTPUT, {"type": "notify", "target": "owner"}, "Send brief"),
        ],
        edges=[
            FlowEdge("trigger", "research"),
            FlowEdge("research", "checked"),
            FlowEdge("checked", "brief"),
        ],
    )


def _code_review_pass(flow_id: str = "code-review-pass") -> Flow:
    """trigger → task(code-review) → branch(verdict) → outputs.

    Teaches the ``branch`` node kind: one upstream verdict, two distinct downstream
    paths chosen by an edge ``when`` condition (no gate involved — this is a plain
    routing branch, the review-loop shape).
    """
    return Flow(
        id=flow_id,
        name="Code Review Pass",
        description=(
            "Review a diff, then BRANCH on the verdict: approved → notify the "
            "owner; changes requested → log the follow-up. Teaches the engine's "
            "``branch`` node kind (one verdict, two conditional edges)."
        ),
        nodes=[
            FlowNode("trigger", NodeKind.TRIGGER, {"type": "manual"}, "Diff in"),
            FlowNode(
                "review",
                NodeKind.TASK,
                {
                    "skill": "code_review",
                    "prompt": (
                        "Review the diff below. End the review with exactly one "
                        "line: 'verdict: approved' or 'verdict: changes_requested'."
                        "\n\n{input}"
                    ),
                },
                "Review the diff",
            ),
            FlowNode("verdict", NodeKind.BRANCH, {}, "Approved or not?"),
            FlowNode(
                "approved",
                NodeKind.OUTPUT,
                {"type": "notify", "target": "owner"},
                "Notify: approved",
            ),
            FlowNode(
                "changes",
                NodeKind.OUTPUT,
                {"type": "log", "target": "review-followups"},
                "Log: changes requested",
            ),
        ],
        edges=[
            FlowEdge("trigger", "review"),
            FlowEdge("review", "verdict"),
            FlowEdge(
                "verdict",
                "approved",
                when={"field": "summary", "op": "contains", "value": "approved"},
            ),
            # Unconditional edge = the else branch (changes requested / anything else).
            FlowEdge("verdict", "changes"),
        ],
    )


def _daily_summary(flow_id: str = "daily-summary") -> Flow:
    """trigger → task(summarize) → output(doc). The simplest possible flow.

    The hello-world of the engine: three nodes, one straight line, no gate, no
    branch — read this one first.
    """
    return Flow(
        id=flow_id,
        name="Daily Summary",
        description=(
            "The simplest possible work-flow: trigger → summarize → write a doc. "
            "No gate, no branch — the hello-world to read before the others."
        ),
        nodes=[
            FlowNode("trigger", NodeKind.TRIGGER, {"type": "schedule", "slot": "daily"}, "Daily tick"),
            FlowNode(
                "summarize",
                NodeKind.TASK,
                {
                    "skill": "summarize",
                    "prompt": "Summarize today's activity in three sentences.\n\n{input}",
                },
                "Summarize the day",
            ),
            FlowNode("doc", NodeKind.OUTPUT, {"type": "doc", "target": "daily-summary"}, "Write the doc"),
        ],
        edges=[
            FlowEdge("trigger", "summarize"),
            FlowEdge("summarize", "doc"),
        ],
    )


def _content_pipeline(flow_id: str = "content-pipeline") -> Flow:
    """trigger → task(draft) → task(critique) → gate(approval) → output.

    Teaches a HITL gate mid-flow: two chained tasks feed a human APPROVAL gate
    (parked via ``security/approval.py``) before anything is published — the
    engine's non-pass-halts-the-path rule made concrete with a person in the loop.
    """
    return Flow(
        id=flow_id,
        name="Content Pipeline",
        description=(
            "Draft → critique → a human APPROVAL gate → publish. Teaches a "
            "human-in-the-loop gate mid-flow (a non-pass halts the path until "
            "the owner approves via `/approve <id>`)."
        ),
        nodes=[
            FlowNode("trigger", NodeKind.TRIGGER, {"type": "manual"}, "Topic in"),
            FlowNode(
                "draft",
                NodeKind.TASK,
                {"skill": "draft", "prompt": "Draft a short piece about: {input}"},
                "Draft the content",
            ),
            FlowNode(
                "critique",
                NodeKind.TASK,
                {
                    "skill": "critique",
                    "prompt": "Critique and tighten the draft below.\n\n{input}",
                },
                "Critique the draft",
            ),
            FlowNode(
                "approval",
                NodeKind.GATE,
                {"type": "approval"},
                "Owner approval",
            ),
            FlowNode("publish", NodeKind.OUTPUT, {"type": "doc", "target": "published"}, "Publish"),
        ],
        edges=[
            FlowEdge("trigger", "draft"),
            FlowEdge("draft", "critique"),
            FlowEdge("critique", "approval"),
            FlowEdge("approval", "publish"),
        ],
    )


FLOW_TEMPLATES: list[dict] = [
    {
        "id": "research-brief",
        "name": "Research Brief",
        "description": _research_brief.__doc__.strip().splitlines()[0],
        "teaches": "task → gate:verify (objective regex check) → output",
        "builder": _research_brief,
    },
    {
        "id": "code-review-pass",
        "name": "Code Review Pass",
        "description": _code_review_pass.__doc__.strip().splitlines()[0],
        "teaches": "task → branch (conditional edges on a verdict) → outputs",
        "builder": _code_review_pass,
    },
    {
        "id": "daily-summary",
        "name": "Daily Summary",
        "description": _daily_summary.__doc__.strip().splitlines()[0],
        "teaches": "trigger → task → output (the three-node hello-world)",
        "builder": _daily_summary,
    },
    {
        "id": "content-pipeline",
        "name": "Content Pipeline",
        "description": _content_pipeline.__doc__.strip().splitlines()[0],
        "teaches": "task → task → gate:approval (a HITL gate mid-flow) → output",
        "builder": _content_pipeline,
    },
]

_BY_ID: dict[str, dict] = {t["id"]: t for t in FLOW_TEMPLATES}


# ── reads ────────────────────────────────────────────────────────────────────────


def list_templates() -> list[dict]:
    """The whole gallery (serialisable) for the CLI / dashboard picker.

    Each item is ``{id, name, description, teaches, node_count, node_kinds}`` — the
    builder itself is not included (not JSON-serialisable); ``node_kinds`` previews
    which engine node kinds the template exercises without instantiating it.
    """
    out: list[dict] = []
    for t in FLOW_TEMPLATES:
        flow = t["builder"]()
        kinds: list[str] = []
        for n in flow.nodes:
            if n.kind.value not in kinds:
                kinds.append(n.kind.value)
        out.append(
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "teaches": t["teaches"],
                "node_count": len(flow.nodes),
                "node_kinds": kinds,
            }
        )
    return out


def get_template(template_id: str) -> dict:
    """One template entry by id. Raises :class:`FlowTemplateError` if unknown."""
    template = _BY_ID.get((template_id or "").strip())
    if template is None:
        raise FlowTemplateError(
            f"unknown flow template {template_id!r}; known: {', '.join(_BY_ID)}"
        )
    return template


def build(template_id: str, flow_id: Optional[str] = None) -> Flow:
    """Build (but do not persist) a VALIDATED :class:`Flow` for ``template_id``.

    ``flow_id`` overrides the template's default id (used by :func:`apply_template`
    to avoid a collision). Raises :class:`FlowTemplateError` for an unknown id; the
    builder itself returns an already-validated flow (each is asserted in tests).
    """
    template = get_template(template_id)
    builder: Callable[..., Flow] = template["builder"]
    flow = builder(flow_id) if flow_id else builder()
    return flow.validate()


# ── instantiation ────────────────────────────────────────────────────────────────


def _fresh_id(store: FlowStore, base_id: str) -> str:
    """The first ``base_id`` / ``base_id-2`` / ``base_id-3`` … not already stored."""
    if store.get(base_id) is None:
        return base_id
    n = 2
    while store.get(f"{base_id}-{n}") is not None:
        n += 1
    return f"{base_id}-{n}"


def apply_template(template_id: str, config: "Optional[Config]" = None) -> Flow:
    """Instantiate ``template_id`` into the :class:`FlowStore` under a FRESH id.

    NEVER overwrites an existing flow: if the template's default id is already
    taken, the new instance is suffixed (``"<id>-2"``, …) rather than clobbering
    the owner's customized flow of the same name — the flows analogue of the org
    templates' idempotent, non-destructive re-apply. Returns the persisted
    :class:`Flow` (with its final, possibly-suffixed id).
    """
    get_template(template_id)  # validates the id first, before any I/O
    store = FlowStore(config)
    flow_id = _fresh_id(store, template_id)
    flow = build(template_id, flow_id=flow_id)
    return store.create(flow)
