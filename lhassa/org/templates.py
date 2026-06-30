"""Use-case ORG TEMPLATES — presets that recreate a whole working structure at once.

A *template* is a named, multi-brain org preset the owner can apply in one move:
"Code Review", "Bug Investigation", "Refactors & Migration", "Incident Response",
and a richer multi-level "Software Factory". Applying one CREATES its brains under
Lhassa — the permanent administrator root ("orbits everything") — via the validated
:mod:`lhassa.org.management` ops, so a bad template can never corrupt the chart.

Design:

  - Each template is a plain, serialisable dict: ``{id, name, description, category,
    brains: [...]}``. A brain is ``{id, role, department?, skills, limits, parent}``.
  - ``parent`` is either ``"__admin__"`` (resolved to ``config.root_admin_id``, i.e.
    Lhassa) or another brain's LOCAL id within the same template (resolved to its
    namespaced id). This keeps templates relocatable and free of hard-coded ids.
  - Instantiation NAMESPACES every brain id as ``"<template_id>:<local_id>"`` so
    applying a template twice (or two templates) never collides, and is idempotent:
    a brain whose namespaced id already exists is skipped.
  - Skills are free-form names (they may not exist yet — that is fine, the org stays
    modular). :func:`ensure_template_skills` can best-effort seed the missing ones.

This is a SHELL module (it composes management/topology/skills); it adds no new core.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from lhassa.org import management
from lhassa.org import topology as topo

if TYPE_CHECKING:
    from lhassa.config import Config

#: The token a brain's ``parent`` uses to attach UNDER the permanent administrator.
ADMIN_REF = "__admin__"


class TemplateError(ValueError):
    """Raised on an unknown template id. A :class:`ValueError` so the API/CLI map it
    to a 4xx / exit-1 with one ``except ValueError``."""


# ── the catalog ──────────────────────────────────────────────────────────────────
# Brains are listed PARENT-FIRST so instantiation can attach children after their
# coordinator. ``parent="__admin__"`` ⇒ directly under Lhassa.

TEMPLATES: list[dict] = [
    {
        "id": "code-review",
        "name": "Code Review",
        "description": "A reviewer plus a security reviewer that read diffs and flag issues.",
        "category": "quality",
        "brains": [
            {
                "id": "reviewer",
                "role": "department",
                "department": "code-review",
                "skills": ["code_review", "readability", "style"],
                "limits": {"max_risk": "low", "read_only": True},
                "parent": ADMIN_REF,
            },
            {
                "id": "security-reviewer",
                "role": "department",
                "department": "security-review",
                "skills": ["security_review", "vuln_scan"],
                "limits": {"max_risk": "low", "read_only": True},
                "parent": ADMIN_REF,
            },
        ],
    },
    {
        "id": "bug-investigation",
        "name": "Bug Investigation",
        "description": "Reproduce, localize and root-cause a defect before any fix.",
        "category": "diagnosis",
        "brains": [
            {
                "id": "reproducer",
                "role": "department",
                "department": "reproduction",
                "skills": ["repro", "testing", "logs"],
                "limits": {"max_risk": "low"},
                "parent": ADMIN_REF,
            },
            {
                "id": "root-cause",
                "role": "department",
                "department": "root-cause",
                "skills": ["debugging", "tracing", "git_bisect"],
                "limits": {"max_risk": "medium"},
                "parent": ADMIN_REF,
            },
        ],
    },
    {
        "id": "refactor-migration",
        "name": "Refactors & Migration",
        "description": "Plan and carry out a large refactor or framework/version migration.",
        "category": "evolution",
        "brains": [
            {
                "id": "planner",
                "role": "general",
                "department": "migration-planning",
                "skills": ["planning", "impact_analysis"],
                "limits": {"max_risk": "medium"},
                "parent": ADMIN_REF,
            },
            {
                "id": "refactorer",
                "role": "department",
                "department": "refactor",
                "skills": ["refactor", "codemod", "testing"],
                "limits": {"max_risk": "medium"},
                "parent": "planner",
            },
            {
                "id": "migrator",
                "role": "department",
                "department": "migration",
                "skills": ["migration", "dependency_upgrade", "testing"],
                "limits": {"max_risk": "high", "require_approval": True},
                "parent": "planner",
            },
        ],
    },
    {
        "id": "incident-response",
        "name": "Incident Response",
        "description": "An incident commander coordinating triage, comms and remediation.",
        "category": "operations",
        "brains": [
            {
                "id": "commander",
                "role": "general",
                "department": "incident-command",
                "skills": ["coordination", "triage"],
                "limits": {"max_risk": "high"},
                "parent": ADMIN_REF,
            },
            {
                "id": "triage",
                "role": "department",
                "department": "triage",
                "skills": ["triage", "monitoring", "logs"],
                "limits": {"max_risk": "medium"},
                "parent": "commander",
            },
            {
                "id": "comms",
                "role": "department",
                "department": "comms",
                "skills": ["status_updates", "stakeholder_comms"],
                "limits": {"max_risk": "low"},
                "parent": "commander",
            },
            {
                "id": "remediation",
                "role": "department",
                "department": "remediation",
                "skills": ["remediation", "rollback", "hotfix"],
                "limits": {"max_risk": "high", "require_approval": True},
                "parent": "commander",
            },
        ],
    },
    {
        "id": "software-factory",
        "name": "Software Factory",
        "description": (
            "A full delivery org: a delivery coordinator over frontend, backend, "
            "QA and devops department brains — recreate a whole software factory."
        ),
        "category": "delivery",
        "brains": [
            {
                "id": "delivery",
                "role": "general",
                "department": "delivery",
                "skills": ["planning", "coordination", "delivery"],
                "limits": {"max_risk": "medium"},
                "parent": ADMIN_REF,
            },
            {
                "id": "frontend",
                "role": "department",
                "department": "frontend",
                "skills": ["frontend", "ui", "accessibility", "testing"],
                "limits": {"max_risk": "low"},
                "parent": "delivery",
            },
            {
                "id": "backend",
                "role": "department",
                "department": "backend",
                "skills": ["backend", "api", "database", "testing"],
                "limits": {"max_risk": "medium"},
                "parent": "delivery",
            },
            {
                "id": "qa",
                "role": "department",
                "department": "qa",
                "skills": ["testing", "qa", "verification", "e2e"],
                "limits": {"max_risk": "low"},
                "parent": "delivery",
            },
            {
                "id": "devops",
                "role": "department",
                "department": "devops",
                "skills": ["devops", "ci", "deployment", "observability"],
                "limits": {"max_risk": "high", "require_approval": True},
                "parent": "delivery",
            },
        ],
    },
]

_BY_ID: dict[str, dict] = {t["id"]: t for t in TEMPLATES}


# ── reads ────────────────────────────────────────────────────────────────────────


def list_templates() -> list[dict]:
    """The whole catalog (serialisable) for the CLI / dashboard picker.

    Each item adds ``brain_count`` and the flat ``skills`` set referenced so the UI
    can preview a template without expanding every brain.
    """
    out: list[dict] = []
    for t in TEMPLATES:
        skills: list[str] = []
        for b in t["brains"]:
            for s in b.get("skills", ()):
                if s not in skills:
                    skills.append(s)
        out.append(
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "category": t["category"],
                "brain_count": len(t["brains"]),
                "skills": skills,
                "brains": [dict(b) for b in t["brains"]],
            }
        )
    return out


def get_template(template_id: str) -> dict:
    """One template by id. Raises :class:`TemplateError` if unknown."""
    template = _BY_ID.get((template_id or "").strip())
    if template is None:
        raise TemplateError(
            f"unknown template {template_id!r}; known: {', '.join(_BY_ID)}"
        )
    return template


# ── instantiation ────────────────────────────────────────────────────────────────


def _namespaced(template_id: str, local_id: str) -> str:
    """The on-chart id for a template-local brain id (``"<template>:<local>"``)."""
    return f"{template_id}:{local_id}"


def _resolve_parent(template_id: str, parent: Optional[str], admin: str) -> str:
    """Resolve a brain's ``parent`` ref to a real chart id.

    ``"__admin__"``/empty ⇒ the permanent administrator (Lhassa); any other value is
    a template-local id ⇒ its namespaced chart id.
    """
    if not parent or parent == ADMIN_REF:
        return admin
    return _namespaced(template_id, parent)


def instantiate(template_id: str, *, config: "Optional[Config]" = None) -> dict:
    """Build a template's brains under Lhassa and return the updated chart.

    Idempotent: a namespaced brain id that already exists is skipped (so re-applying
    a template is safe). Each brain is created via :func:`management.add_brain`, which
    validates the whole topology BEFORE persisting — so a bad brain raises and never
    corrupts the saved chart. Brains are listed parent-first, so a coordinator exists
    before its children attach.

    Returns ``{"template", "created": [ids], "skipped": [ids], "chart": describe()}``.
    """
    template = get_template(template_id)
    admin = topo.admin_id(config)

    created: list[str] = []
    skipped: list[str] = []
    for brain in template["brains"]:
        real_id = _namespaced(template_id, brain["id"])
        # Re-read each loop so we see brains added earlier in this same call.
        topology = topo.load_topology(config)
        if topology.has(real_id):
            skipped.append(real_id)
            continue
        management.add_brain(
            real_id,
            role=brain.get("role", "department"),
            parent=_resolve_parent(template_id, brain.get("parent"), admin),
            department=brain.get("department"),
            skills=brain.get("skills", ()),
            limits=brain.get("limits"),
            config=config,
        )
        created.append(real_id)

    return {
        "template": template_id,
        "created": created,
        "skipped": skipped,
        "chart": management.describe(config),
    }


def ensure_template_skills(
    template_id: str, config: "Optional[Config]" = None
) -> list[str]:
    """Best-effort: seed any of the template's referenced skills that are missing.

    Creates a minimal SKILL.md for each unseen skill name via :mod:`lhassa.skills`.
    Never hard-depends on the skills layer or raises — on any error it returns the
    skills created so far. Returns the list of skill names newly seeded.
    """
    template = get_template(template_id)
    names: list[str] = []
    for brain in template["brains"]:
        for s in brain.get("skills", ()):
            if s not in names:
                names.append(s)

    created: list[str] = []
    try:
        from lhassa.skills import SkillStore, template_skill

        store = SkillStore(config)
        for name in names:
            try:
                if store.get(name) is None:
                    store.create(
                        template_skill(name, f"Skill used by the {template['name']} org template.")
                    )
                    created.append(name)
            except Exception:
                continue
    except Exception:  # pragma: no cover - skills layer optional
        return created
    return created
