# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI operations + the offline seed CATALOG for skills.

Three capabilities power the admin surface's "create/improve WITH AI" and
"recommend skills" requests:

* :func:`create_skill_with_ai`: draft a SKILL.md from a short brief.
* :func:`improve_skill_with_ai`: load an existing skill and return a clearer,
  better-described version (the caller decides whether to save it).
* :func:`recommend_skills`: recommend skills from a built-in CATALOG combined
  with what is NOT yet installed. This one is **deterministic / offline** (no
  model), so it always works.

The two model-driven ops use a :class:`~lhassa.llm.provider.ModelProvider` behind
an **injectable** ``provider`` seam (tests pass a fake; nothing real is called).
They NEVER raise: with no provider/creds they degrade to a clear result; a
template/unchanged skill whose ``error`` reads ``"AI unavailable: …"``: so the
caller can always show something useful.

:func:`ensure_seed` writes a couple of real starter skills into a store on
demand (explicit call, tested against a temp dir; never the real ``~/.lhassa``
at import time).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.llm.provider import get_model_provider
from lhassa.skills.skillmd import (
    Skill,
    parse_skill_md,
    slugify,
    strip_code_fence,
    template_skill,
)
from lhassa.skills.store import SkillStore

if TYPE_CHECKING:
    from lhassa.config import Config
    from lhassa.llm.provider import ModelProvider

__all__ = [
    "CATALOG",
    "catalog_skill",
    "install_from_catalog",
    "recommend_skills",
    "ensure_seed",
    "create_skill_with_ai",
    "improve_skill_with_ai",
]


# ── the seed CATALOG (offline, Lhassa-relevant) ───────────────────────────────
# Inspired by the awesome-agent-skills categories but trimmed to what is useful
# for a personal do-and-sleep brain. Each entry is enough to (a) recommend and
# (b) materialise as a real SKILL.md via :func:`catalog_skill`.
CATALOG: list[dict] = [
    {
        "name": "optimization",
        "description": "Make a routine cheaper on one clean lower-is-better metric "
        "(tokens, time, or steps) without changing what it produces.",
        "domain": "optimization",
        "tags": ["performance", "cost"],
        "level": 2,
        "allowed-tools": ["read_file", "write_file", "shell"],
        "instructions": (
            "# optimization\n\n"
            "Operate as the OPTIMIZATION specialist: make an existing routine cheaper "
            "on ONE clean, lower-is-better metric; tokens, wall-clock time, or number "
            "of steps; without changing what it produces.\n\n"
            "## When to use\n\n"
            "Something works but is slow, expensive, or wasteful and there is a "
            "measurable baseline to beat.\n\n"
            "## How to apply\n\n"
            "- Always measure against a concrete baseline; never claim 'better' without "
            "a number that beats it.\n"
            "- Prefer the smallest change that moves the metric.\n"
            "- Never trade correctness for speed: a faster wrong answer fails.\n"
            "- Report the metric before and after, plainly.\n"
        ),
    },
    {
        "name": "documentation",
        "description": "Write or refresh clear, accurate docs (READMEs, guides, "
        "comments) for code or a process.",
        "domain": "documentation",
        "tags": ["writing", "docs"],
        "level": 1,
        "allowed-tools": ["read_file", "write_file"],
        "instructions": (
            "# documentation\n\n"
            "Produce clear, accurate documentation grounded in the actual code or "
            "process; never invent behaviour.\n\n"
            "## When to use\n\n"
            "A change ships undocumented, or existing docs drifted from reality.\n\n"
            "## How to apply\n\n"
            "- Read the source of truth first; document what IS, not what should be.\n"
            "- Lead with the reader's task; keep examples runnable.\n"
            "- Prefer short sections with headings over walls of prose.\n"
        ),
    },
    {
        "name": "code-review",
        "description": "Review a diff for correctness, clarity, and risk; flag concrete "
        "issues with actionable fixes.",
        "domain": "code-review",
        "tags": ["quality", "review"],
        "level": 2,
        "allowed-tools": ["read_file", "shell"],
        "instructions": (
            "# code-review\n\n"
            "Review a change for correctness, clarity, and risk. Be specific and "
            "actionable.\n\n"
            "## When to use\n\n"
            "Before merging a diff, or when asked to audit a change.\n\n"
            "## How to apply\n\n"
            "- Read the diff in full and the surrounding context it touches.\n"
            "- Flag correctness bugs first, then clarity/maintainability.\n"
            "- Every finding names the file, the problem, and a concrete fix.\n"
            "- Note what is GOOD too, so the signal is trustworthy.\n"
        ),
    },
    {
        "name": "research",
        "description": "Gather, cross-check, and synthesise information on a question "
        "into a sourced summary.",
        "domain": "research",
        "tags": ["search", "synthesis"],
        "level": 2,
        "allowed-tools": ["web_search", "read_file"],
        "instructions": (
            "# research\n\n"
            "Answer a question by gathering and cross-checking multiple sources, then "
            "synthesising a concise, sourced summary.\n\n"
            "## When to use\n\n"
            "A decision or answer needs facts you do not already hold.\n\n"
            "## How to apply\n\n"
            "- Break the question into sub-questions; search each.\n"
            "- Prefer primary sources; corroborate claims across at least two.\n"
            "- Separate what is established from what is uncertain.\n"
        ),
    },
    {
        "name": "testing",
        "description": "Design and write focused tests that pin down behaviour and catch "
        "regressions.",
        "domain": "testing",
        "tags": ["quality", "tests"],
        "level": 2,
        "allowed-tools": ["read_file", "write_file", "shell"],
        "instructions": (
            "# testing\n\n"
            "Design and write focused tests that pin down behaviour and would catch a "
            "regression.\n\n"
            "## When to use\n\n"
            "New behaviour ships untested, or a bug needs a failing test first.\n\n"
            "## How to apply\n\n"
            "- Cover the happy path, the edges, and the failure modes.\n"
            "- One behaviour per test; make the assertion meaningful.\n"
            "- Make the test fail first, then pass; never assert nothing.\n"
        ),
    },
    {
        "name": "security-audit",
        "description": "Inspect code or config for security weaknesses (secrets, injection, "
        "unsafe defaults) and propose fixes.",
        "domain": "security",
        "tags": ["security", "audit"],
        "level": 3,
        "allowed-tools": ["read_file", "shell"],
        "instructions": (
            "# security-audit\n\n"
            "Inspect code or configuration for security weaknesses and propose concrete "
            "fixes.\n\n"
            "## When to use\n\n"
            "Before exposing a surface, or when handling secrets / untrusted input.\n\n"
            "## How to apply\n\n"
            "- Look for hard-coded secrets, injection, unsafe deserialisation, and "
            "permissive defaults.\n"
            "- Rank findings by impact and likelihood.\n"
            "- Give a minimal, concrete remediation for each.\n"
        ),
    },
    {
        "name": "data-cleanup",
        "description": "Normalise, de-duplicate, and validate messy data into a clean, "
        "consistent shape.",
        "domain": "data",
        "tags": ["data", "cleanup"],
        "level": 1,
        "allowed-tools": ["read_file", "write_file", "shell"],
        "instructions": (
            "# data-cleanup\n\n"
            "Turn messy data into a clean, consistent, validated shape.\n\n"
            "## When to use\n\n"
            "Records are inconsistent, duplicated, or partially malformed.\n\n"
            "## How to apply\n\n"
            "- Define the target schema first; validate against it.\n"
            "- Normalise formats, de-duplicate, and quarantine the unfixable.\n"
            "- Report what changed and what was dropped.\n"
        ),
    },
    # ── the wider catalog (templated bodies derived from the description) ──────
    {
        "name": "refactor",
        "description": "Restructure code for clarity without changing its behaviour, "
        "kept safe by tests.",
        "domain": "refactor",
        "tags": ["code", "cleanup"],
        "level": 2,
        "allowed-tools": ["read_file", "write_file", "shell"],
    },
    {
        "name": "migration",
        "description": "Move code, schema, or data to a new version or system with a "
        "reversible, staged plan.",
        "domain": "migration",
        "tags": ["data", "schema"],
        "level": 3,
        "allowed-tools": ["read_file", "write_file", "shell"],
    },
    {
        "name": "incident-triage",
        "description": "Stabilise a live incident: assess blast radius, mitigate first, "
        "then find root cause.",
        "domain": "incident",
        "tags": ["ops", "triage"],
        "level": 3,
        "allowed-tools": ["read_file", "shell"],
    },
    {
        "name": "api-design",
        "description": "Design clear, consistent, versionable API contracts with sane "
        "errors and pagination.",
        "domain": "api",
        "tags": ["design", "interface"],
        "level": 2,
        "allowed-tools": ["read_file", "write_file"],
    },
    {
        "name": "sql-expert",
        "description": "Write, explain, and optimise SQL queries and schemas for "
        "correctness and speed.",
        "domain": "data",
        "tags": ["sql", "database"],
        "level": 2,
        "allowed-tools": ["read_file", "shell"],
    },
    {
        "name": "devops-ci",
        "description": "Build and harden CI/CD pipelines: reproducible builds, caching, "
        "and gated deploys.",
        "domain": "devops",
        "tags": ["ci", "automation"],
        "level": 2,
        "allowed-tools": ["read_file", "write_file", "shell"],
    },
    {
        "name": "dependency-audit",
        "description": "Inventory dependencies, flag vulnerable or stale ones, and plan "
        "safe upgrades.",
        "domain": "security",
        "tags": ["dependencies", "audit"],
        "level": 2,
        "allowed-tools": ["read_file", "shell"],
    },
    {
        "name": "pr-summarizer",
        "description": "Summarise a pull request: what changed, why, and what a reviewer "
        "should watch for.",
        "domain": "review",
        "tags": ["git", "summary"],
        "level": 1,
        "allowed-tools": ["read_file", "shell"],
    },
    {
        "name": "bug-repro",
        "description": "Turn a vague bug report into a minimal, deterministic reproduction "
        "and a failing test.",
        "domain": "debugging",
        "tags": ["bug", "repro"],
        "level": 2,
        "allowed-tools": ["read_file", "write_file", "shell"],
    },
    {
        "name": "performance-profiling",
        "description": "Profile a hot path to find the real bottleneck before changing any "
        "code.",
        "domain": "performance",
        "tags": ["profiling", "performance"],
        "level": 2,
        "allowed-tools": ["read_file", "shell"],
    },
    {
        "name": "accessibility-audit",
        "description": "Audit a UI for accessibility (contrast, labels, keyboard, ARIA) "
        "against WCAG and fix gaps.",
        "domain": "accessibility",
        "tags": ["a11y", "audit"],
        "level": 2,
        "allowed-tools": ["read_file", "write_file"],
    },
    {
        "name": "i18n",
        "description": "Internationalise strings and formats so the product localises "
        "cleanly across locales.",
        "domain": "i18n",
        "tags": ["localization", "i18n"],
        "level": 1,
        "allowed-tools": ["read_file", "write_file"],
    },
    {
        "name": "release-notes",
        "description": "Turn a set of merged changes into clear, user-facing release notes "
        "and a changelog entry.",
        "domain": "documentation",
        "tags": ["release", "changelog"],
        "level": 1,
        "allowed-tools": ["read_file", "shell"],
    },
    {
        "name": "web-scraping",
        "description": "Extract structured data from web pages politely and robustly, "
        "respecting robots and rate limits.",
        "domain": "data",
        "tags": ["scraping", "web"],
        "level": 2,
        "allowed-tools": ["web_search", "read_file", "write_file"],
    },
    {
        "name": "prompt-optimizer",
        "description": "Tighten an LLM prompt for reliability and cost: clearer task, "
        "guardrails, fewer tokens.",
        "domain": "prompts",
        "tags": ["llm", "prompt"],
        "level": 2,
        "allowed-tools": ["read_file", "write_file"],
    },
]

# The starter skills seeded on demand: the optimization one mirrors the existing
# specialist, plus two broadly-useful generics.
_SEED_NAMES = ("optimization", "documentation", "code-review")


def _catalog_body(entry: dict) -> str:
    """A sensible SKILL.md instructions body templated from a catalog ``entry``.

    Used for catalog rows that ship only a one-line description (no hand-written
    ``instructions``): turns the description + domain into a real, structured body
    so an installed skill carries usable guidance, not an empty file.
    """
    name = slugify(entry["name"])
    desc = (entry.get("description") or "").strip()
    domain = (entry.get("domain") or "").strip()
    focus = domain or name
    when = f"the task calls for {focus} work"
    if desc:
        when += f": {desc[0].lower() + desc[1:]}"
    goal = desc or f"do the {focus} work well"
    return (
        f"# {name}\n\n"
        f"{desc or f'Operate as the {focus} specialist.'}\n\n"
        "## When to use\n\n"
        f"Load this skill when {when}\n\n"
        "## How to apply\n\n"
        f"- Keep the goal in view: {goal}.\n"
        "- Work in the smallest correct steps; check each against reality.\n"
        "- Never trade correctness for speed; a fast wrong answer still fails.\n"
        "- Report what you did, what you found, and what is left; plainly.\n"
    )


def catalog_skill(entry: dict) -> Skill:
    """Materialise a CATALOG ``entry`` into a full :class:`Skill` (with SKILL.md body).

    A row may ship a hand-written ``instructions`` body; rows that only carry a
    one-line description get a sensible body templated by :func:`_catalog_body`, so
    every catalog skill installs as a real, usable SKILL.md.
    """
    metadata: dict = {"source": "lhassa/catalog"}
    if entry.get("domain"):
        metadata["domain"] = entry["domain"]
    if entry.get("tags"):
        metadata["tags"] = list(entry["tags"])
    if entry.get("level") is not None:
        metadata["level"] = entry["level"]
    if entry.get("allowed-tools"):
        metadata["allowed-tools"] = list(entry["allowed-tools"])
    return Skill(
        name=slugify(entry["name"]),
        description=entry.get("description", ""),
        instructions=(entry.get("instructions") or "").strip() or _catalog_body(entry),
        metadata=metadata,
        resources=[],
    )


def _catalog_by_name() -> dict[str, dict]:
    """The CATALOG indexed by slugified name (built fresh; the catalog is small)."""
    return {slugify(entry["name"]): entry for entry in CATALOG}


def install_from_catalog(
    name: str, *, config: "Optional[Config]" = None
) -> "Optional[Skill]":
    """Materialise catalog skill ``name`` into the store as a real SKILL.md.

    This is the bridge from a brain carrying a skill NAME to the skill actually
    DOING something: it writes ``<skills_dir>/<name>/SKILL.md`` (templated from the
    catalog entry) so the executor can later load its instructions into a prompt.

    Idempotent: if the skill is already installed it is returned unchanged (nothing
    is overwritten). Returns ``None`` when ``name`` is not in the catalog.
    """
    slug = slugify(name)
    entry = _catalog_by_name().get(slug)
    if entry is None:
        return None
    store = SkillStore(config)
    existing = store.get(slug)
    if existing is not None:
        return existing  # already installed; skip (idempotent)
    skill = catalog_skill(entry)
    store.save(skill)
    return store.get(slug) or skill


# ── recommend (deterministic / offline) ───────────────────────────────────────


def recommend_skills(
    context: Optional[str] = None, *, config: "Optional[Config]" = None
) -> list[dict]:
    """Recommend catalog skills that are NOT yet installed. Offline, never raises.

    Each result is ``{"name", "description", "domain", "tags", "installed",
    "score", "reason"}``. When ``context`` is given, entries whose
    name/description/domain/tags match its words score higher and rank first;
    otherwise the catalog order is preserved. Already-installed catalog skills are
    omitted (nothing to recommend).
    """
    try:
        installed = {s.name for s in SkillStore(config).list_skills() if s.name}
    except Exception as exc:  # pragma: no cover - store reads are defensive already
        logger.warning("skills: recommend could not read the store; {}", exc)
        installed = set()

    words = {w for w in (context or "").lower().replace(",", " ").split() if w}
    recs: list[dict] = []
    for order, entry in enumerate(CATALOG):
        name = slugify(entry["name"])
        if name in installed:
            continue
        haystack = " ".join(
            [
                name,
                entry.get("description", ""),
                entry.get("domain", ""),
                " ".join(entry.get("tags", []) or []),
            ]
        ).lower()
        matches = sorted(w for w in words if w in haystack)
        # Higher score ranks first: keyword matches dominate, catalog order breaks ties.
        score = len(matches) * 100 + (len(CATALOG) - order)
        reason = (
            f"matches {', '.join(matches)}" if matches else "not yet installed"
        )
        recs.append(
            {
                "name": name,
                "description": entry.get("description", ""),
                "domain": entry.get("domain", ""),
                "tags": list(entry.get("tags", []) or []),
                "installed": False,
                "score": score,
                "reason": reason,
            }
        )
    recs.sort(key=lambda r: (-r["score"], r["name"]))
    return recs


# ── seed (explicit, never at import time) ─────────────────────────────────────


def ensure_seed(config: "Optional[Config]" = None) -> list[str]:
    """Seed the starter skills into the store if they are missing. Returns the names seeded.

    Idempotent: skills already present are left untouched. An explicit call (never
    runs at import), so tests drive it against a temp ``skills_dir``.
    """
    store = SkillStore(config)
    by_name = {entry["name"]: entry for entry in CATALOG}
    seeded: list[str] = []
    for name in _SEED_NAMES:
        entry = by_name.get(name)
        if entry is None:
            continue
        if store.get(name) is None:
            store.save(catalog_skill(entry))
            seeded.append(name)
    return seeded


# ── AI ops (injectable provider seam; degrade, never raise) ───────────────────

_CREATE_SYSTEM = (
    "You author SKILL.md files for an AI assistant's skill library. A SKILL.md "
    "has YAML frontmatter with 'name' (a kebab-case slug) and 'description' (one "
    "line that decides WHEN the skill loads), an optional nested 'metadata' block "
    "(allowed-tools, domain, tags, level), then a markdown body of instructions "
    "(what the skill does and how to apply it). Respond with ONLY the SKILL.md "
    "document, starting with '---'. Do not add commentary."
)

_IMPROVE_SYSTEM = (
    "You improve an existing SKILL.md for an AI assistant's skill library: make "
    "the description sharper (it decides WHEN the skill loads) and the instructions "
    "clearer, more actionable, and better organised, WITHOUT changing the skill's "
    "purpose or its 'name'. Respond with ONLY the improved SKILL.md document, "
    "starting with '---'. Do not add commentary."
)


def _resolve_provider(
    provider: "Optional[ModelProvider]", config: "Optional[Config]"
) -> "ModelProvider":
    if provider is not None:
        return provider
    # Module-bound so tests can monkeypatch ``lhassa.skills.ai.get_model_provider``
    # to exercise the degrade path without a real model.
    return get_model_provider(_resolve_config_obj(config))


def _resolve_config_obj(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


async def create_skill_with_ai(
    brief: str,
    *,
    config: "Optional[Config]" = None,
    provider: "Optional[ModelProvider]" = None,
) -> Skill:
    """Draft a :class:`Skill` from a short ``brief`` using the model. NEVER raises.

    With no provider/creds it returns a deterministic TEMPLATE skill whose
    ``error`` reads ``"AI unavailable: …"`` (still a valid, saveable SKILL.md).
    The model's output is parsed defensively; a missing name falls back to the
    brief's slug.
    """
    fallback = template_skill(brief[:48] or "new-skill", brief)
    try:
        prov = _resolve_provider(provider, config)
    except Exception as exc:
        return replace(fallback, error=f"AI unavailable: {exc}")

    messages = [
        {
            "role": "user",
            "content": f"Create a skill for this brief:\n\n{brief}",
        }
    ]
    try:
        completion = await prov.complete(
            messages, system=_CREATE_SYSTEM, max_tokens=1024
        )
    except Exception as exc:
        return replace(fallback, error=f"AI unavailable: {exc}")

    skill = parse_skill_md(strip_code_fence(completion.text or ""))
    if not (skill.name or "").strip():
        skill = replace(skill, name=fallback.name)
    if not (skill.description or "").strip():
        skill = replace(skill, description=brief.strip()[:160])
    if not (skill.instructions or "").strip():
        # Model gave nothing usable; fall back to the template body but keep going.
        return replace(fallback, error="AI returned no usable instructions")
    return skill


async def improve_skill_with_ai(
    name: str,
    *,
    config: "Optional[Config]" = None,
    provider: "Optional[ModelProvider]" = None,
) -> Skill:
    """Return an improved version of skill ``name``. NEVER raises; caller saves it.

    With no provider/creds (or an empty model answer) it returns the ORIGINAL
    skill unchanged with ``error`` set, so the caller can degrade gracefully.
    Missing skill → a clear error skill.
    """
    store = SkillStore(config)
    existing = store.get(name)
    if existing is None:
        return Skill(name=slugify(name), error=f"no skill named {name!r} to improve")

    try:
        prov = _resolve_provider(provider, config)
    except Exception as exc:
        return replace(existing, error=f"AI unavailable: {exc}")

    from lhassa.skills.skillmd import to_skill_md

    messages = [
        {
            "role": "user",
            "content": "Improve this SKILL.md:\n\n" + to_skill_md(existing),
        }
    ]
    try:
        completion = await prov.complete(
            messages, system=_IMPROVE_SYSTEM, max_tokens=1024
        )
    except Exception as exc:
        return replace(existing, error=f"AI unavailable: {exc}")

    improved = parse_skill_md(strip_code_fence(completion.text or ""))
    if not (improved.instructions or "").strip():
        return replace(existing, error="AI returned no usable improvement")
    # Keep the original identity + bundled resources; the improvement is content.
    return replace(
        improved,
        name=existing.name,
        description=(improved.description or existing.description),
        resources=list(existing.resources),
    )
