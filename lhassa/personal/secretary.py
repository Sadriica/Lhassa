# SPDX-License-Identifier: AGPL-3.0-or-later
"""Personal secretary department and its canonical briefing flows.

The department is stable: ``personal`` owns owner-facing agenda, check-ins and
follow-ups. The flows vary by intent: morning prepares the day; evening closes
the day and prepares tomorrow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.flows.model import Flow, FlowEdge, FlowNode, NodeKind
from lhassa.flows.store import FlowStore
from lhassa.org import management
from lhassa.org.topology import load_topology

if TYPE_CHECKING:
    from lhassa.config import Config


PERSONAL_DEPARTMENT_ID = "personal"
PERSONAL_DEPARTMENT = "personal"
MORNING_FLOW_ID = "personal-morning-briefing"
EVENING_FLOW_ID = "personal-evening-briefing"


def _ensure_personal_department(config: "Optional[Config]" = None) -> bool:
    """Ensure the stable personal department exists. Returns True if created."""
    topology = load_topology(config)
    if topology.has(PERSONAL_DEPARTMENT_ID):
        return False
    management.add_brain(
        PERSONAL_DEPARTMENT_ID,
        role="department",
        department=PERSONAL_DEPARTMENT,
        skills=["personal", "planning", "memory"],
        limits={
            "scope": "owner agenda, check-ins, priorities, reminders and follow-ups",
            "max_risk": "low",
            "read_first": True,
        },
        config=config,
    )
    return True


def morning_flow(flow_id: str = MORNING_FLOW_ID) -> Flow:
    """Prepare the day: read signals, rank priorities, ask the morning question."""
    return Flow(
        id=flow_id,
        name="Personal Morning Briefing",
        description=(
            "The personal department prepares the day: yesterday's check-in, "
            "today's local agenda, priorities, and the morning owner question."
        ),
        nodes=[
            FlowNode("trigger", NodeKind.TRIGGER, {"type": "schedule", "slot": "morning"}, "Morning digest"),
            FlowNode(
                "collect",
                NodeKind.TASK,
                {
                    "brain": PERSONAL_DEPARTMENT_ID,
                    "skill": "personal",
                    "prompt": (
                        "Collect the latest owner check-in, pending approvals, "
                        "tasks due today, reminders, calendar context when available, "
                        "and today's spend. Keep facts exact."
                    ),
                },
                "Collect personal signals",
            ),
            FlowNode(
                "prioritize",
                NodeKind.TASK,
                {
                    "brain": PERSONAL_DEPARTMENT_ID,
                    "skill": "planning",
                    "prompt": (
                        "Rank at most three priorities for today. Prefer urgent "
                        "approvals, due tasks, and owner-stated focus."
                    ),
                },
                "Rank today's priorities",
            ),
            FlowNode(
                "compose",
                NodeKind.TASK,
                {
                    "brain": PERSONAL_DEPARTMENT_ID,
                    "skill": "personal",
                    "prompt": (
                        "Compose a concise morning briefing in Lhassa's first-person "
                        "voice. End by asking how the owner slept and what to protect today."
                    ),
                },
                "Compose morning briefing",
            ),
            FlowNode("output", NodeKind.OUTPUT, {"type": "notify", "target": "owner"}, "Send digest"),
        ],
        edges=[
            FlowEdge("trigger", "collect"),
            FlowEdge("collect", "prioritize"),
            FlowEdge("prioritize", "compose"),
            FlowEdge("compose", "output"),
        ],
    )


def evening_flow(flow_id: str = EVENING_FLOW_ID) -> Flow:
    """Close the day: review outcomes, carry forward, ask tomorrow's memory question."""
    return Flow(
        id=flow_id,
        name="Personal Evening Briefing",
        description=(
            "The personal department closes the day: what happened, what remains, "
            "what moves to tomorrow, and what Lhassa should remember."
        ),
        nodes=[
            FlowNode("trigger", NodeKind.TRIGGER, {"type": "schedule", "slot": "evening"}, "Evening digest"),
            FlowNode(
                "review",
                NodeKind.TASK,
                {
                    "brain": PERSONAL_DEPARTMENT_ID,
                    "skill": "memory",
                    "prompt": (
                        "Review today's important episodes, completed work, pending "
                        "tasks, approvals, and the latest owner check-in."
                    ),
                },
                "Review the day",
            ),
            FlowNode(
                "carry",
                NodeKind.TASK,
                {
                    "brain": PERSONAL_DEPARTMENT_ID,
                    "skill": "planning",
                    "prompt": (
                        "Identify what should carry forward to tomorrow. Keep the "
                        "list short and practical."
                    ),
                },
                "Carry forward",
            ),
            FlowNode(
                "compose",
                NodeKind.TASK,
                {
                    "brain": PERSONAL_DEPARTMENT_ID,
                    "skill": "personal",
                    "prompt": (
                        "Compose a concise evening briefing in Lhassa's first-person "
                        "voice. End by asking how the day went and what to remember for tomorrow."
                    ),
                },
                "Compose evening briefing",
            ),
            FlowNode("output", NodeKind.OUTPUT, {"type": "notify", "target": "owner"}, "Send digest"),
        ],
        edges=[
            FlowEdge("trigger", "review"),
            FlowEdge("review", "carry"),
            FlowEdge("carry", "compose"),
            FlowEdge("compose", "output"),
        ],
    )


def ensure_personal_secretary(config: "Optional[Config]" = None) -> dict:
    """Create/update the personal department and canonical morning/evening flows."""
    summary = {
        "department": PERSONAL_DEPARTMENT_ID,
        "department_created": False,
        "flows": [MORNING_FLOW_ID, EVENING_FLOW_ID],
        "flows_saved": [],
        "errors": [],
    }
    try:
        summary["department_created"] = _ensure_personal_department(config)
    except Exception as exc:
        logger.warning("personal secretary: department scaffold failed: {}", exc)
        summary["errors"].append(f"department: {type(exc).__name__}: {exc}")

    store = FlowStore(config)
    for flow in (morning_flow(), evening_flow()):
        try:
            store.save(flow)
            summary["flows_saved"].append(flow.id)
        except Exception as exc:
            logger.warning("personal secretary: flow save failed for {}: {}", flow.id, exc)
            summary["errors"].append(f"flow {flow.id}: {type(exc).__name__}: {exc}")
    return summary
