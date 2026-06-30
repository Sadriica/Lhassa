"""First-class SKILLS for Lhassa (slice M).

A *skill* is a reusable, model-agnostic capability bundle the brain (and any
org brain/agent) can pick up: a one-line description that decides *when* it
loads plus markdown instructions for *how* to apply it, optionally bundling
resource files. Skills are the portable, shareable cousin of the in-code
:mod:`lhassa.agents.specialists` registry — authored by hand, imported from the
community (the SKILL.md / Agent-Skills convention), or drafted/improved by the
model itself.

This package is additive and self-contained:

* :mod:`lhassa.skills.skillmd` — the :class:`Skill` model + SKILL.md (YAML
  frontmatter + markdown body) and JSON (de)serialisation. Never raises on a
  malformed file — it degrades to a :class:`Skill` carrying a clear ``error``.
* :mod:`lhassa.skills.store` — :class:`SkillStore` over ``config.skills_dir``
  (list/get/create/save/delete/import/export) plus the org bridges
  ``brains_with_skill`` / ``assign_to_brain``.
* :mod:`lhassa.skills.ai` — the AI operations (draft/improve a skill with the
  model behind an injectable provider seam, degrading cleanly with no creds)
  plus an offline seed CATALOG, ``recommend_skills`` and ``ensure_seed``.

The CLI surface lives in :mod:`lhassa.cli.skills_cmds` (the integrator registers
it as ``lhassa skills``).
"""

from __future__ import annotations

from lhassa.skills.skillmd import (
    Skill,
    parse_skill_md,
    skill_from_dict,
    skill_to_dict,
    slugify,
    template_skill,
    to_skill_md,
)
from lhassa.skills.store import (
    SkillStore,
    assign_to_brain,
    brains_with_skill,
)

__all__ = [
    "Skill",
    "parse_skill_md",
    "to_skill_md",
    "skill_from_dict",
    "skill_to_dict",
    "slugify",
    "template_skill",
    "SkillStore",
    "brains_with_skill",
    "assign_to_brain",
]
