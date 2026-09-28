# SPDX-License-Identifier: AGPL-3.0-or-later
"""The :class:`Skill` model + SKILL.md / JSON (de)serialisation.

A skill follows the **SKILL.md** convention (skills.md / Agent-Skills /
awesome-agent-skills): a folder ``<skills_dir>/<name>/SKILL.md`` whose YAML
frontmatter carries ``name`` / ``description`` (+ optional ``license`` and a
nested ``metadata`` block) and whose markdown body is the instructions::

    ---
    name: optimization
    description: Make a routine cheaper on one clean lower-is-better metric.
    license: MIT
    metadata:
      allowed-tools: [read_file, write_file, shell]
      domain: optimization
      tags: [performance, cost]
      level: 2
      source: lhassa/catalog
    ---

    <markdown instructions…>

The same fields round-trip through a home-grown **JSON** form (``skill.json``)
for programmatic use.

Robustness is a hard requirement: :func:`parse_skill_md` NEVER raises on a
slightly-malformed file. It returns a best-effort :class:`Skill` whose ``error``
attribute explains what went wrong, so the store/CLI can surface it instead of
crashing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml
from loguru import logger

__all__ = [
    "Skill",
    "parse_skill_md",
    "to_skill_md",
    "skill_from_dict",
    "skill_to_dict",
    "slugify",
    "template_skill",
]

# Frontmatter keys that live at the TOP level of the YAML (outside the nested
# ``metadata`` block). Everything else round-trips inside ``metadata``.
_TOP_LEVEL_META = ("license",)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n```\s*$", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    """One reusable capability bundle (immutable).

    Fields:
      - ``name``        : the skill's kebab-slug id (also the folder name).
      - ``description`` : the one line used to decide WHEN the skill loads.
      - ``instructions``: the markdown body: what the skill does + how to apply
        it (may reference bundled ``resources`` by filename).
      - ``metadata``    : free-form mapping (``allowed-tools``, ``domain``,
        ``tags``, ``level``, ``source``, ``license`` …). Kept verbatim so unknown
        keys survive a round-trip.
      - ``resources``   : filenames bundled alongside SKILL.md in the folder,
        LISTED but not interpreted.
      - ``error``       : ``None`` when parsed cleanly; otherwise a clear,
        human-readable note about what was malformed (the file still loads).

    Frozen so a stored instance can't be mutated in place; use
    :func:`dataclasses.replace` to derive a changed copy.
    """

    name: str = ""
    description: str = ""
    instructions: str = ""
    metadata: dict = field(default_factory=dict)
    resources: list = field(default_factory=list)
    error: Optional[str] = None


def slugify(value: str) -> str:
    """Lower-case kebab slug of ``value`` (``"Code Review!" -> "code-review"``)."""
    return _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-")


def strip_code_fence(text: str) -> str:
    """Unwrap a single fenced code block, if the whole text is one.

    Models often answer with the SKILL.md wrapped in ```` ```md … ``` ````; this
    peels exactly one outer fence and leaves un-fenced text untouched.
    """
    if not text:
        return text or ""
    m = _FENCE_RE.match(text.strip())
    return m.group(1) if m else text


# ── frontmatter splitting ─────────────────────────────────────────────────────


def _split_frontmatter(text: str) -> tuple[Optional[str], str]:
    """Split ``text`` into ``(frontmatter_yaml | None, body)``.

    A document opens frontmatter with a line that is exactly ``---`` and closes
    it with the next ``---`` / ``...`` line. Anything else (no opener, no closer)
    is treated as a body with no frontmatter; degrade, never raise.
    """
    if not text:
        return None, ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            fm = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1:])
            return fm, body
    # No closing delimiter; treat the whole thing as a plain body.
    return None, text


# ── parse / serialise: SKILL.md ───────────────────────────────────────────────


def parse_skill_md(text: str) -> Skill:
    """Parse SKILL.md ``text`` into a :class:`Skill`. NEVER raises.

    On malformed YAML / a non-mapping frontmatter / a bad ``metadata`` block the
    returned skill carries a clear ``error`` and whatever fields could still be
    recovered (e.g. the body always loads as instructions).
    """
    error: Optional[str] = None
    fm_raw, body = _split_frontmatter(text or "")
    fm: dict = {}
    if fm_raw is not None:
        try:
            loaded = yaml.safe_load(fm_raw)
        except Exception as exc:  # malformed YAML; degrade, keep the body
            loaded = None
            error = f"invalid YAML frontmatter: {exc}"
            logger.warning("skills: malformed SKILL.md frontmatter; {}", exc)
        if isinstance(loaded, dict):
            fm = loaded
        elif loaded is not None and error is None:
            error = "frontmatter is not a mapping"
    else:
        error = "no YAML frontmatter (using the whole file as instructions)"

    name = str(fm.get("name") or "").strip()
    description = str(fm.get("description") or "").strip()

    metadata: dict = {}
    nested = fm.get("metadata")
    if isinstance(nested, dict):
        metadata.update(nested)
    elif nested is not None:
        error = error or "metadata is not a mapping"
    # Any other top-level frontmatter key (e.g. license) folds into metadata so
    # it round-trips; name/description/metadata are handled above.
    for key, val in fm.items():
        if key in ("name", "description", "metadata"):
            continue
        metadata[key] = val

    return Skill(
        name=name,
        description=description,
        instructions=(body or "").strip("\n"),
        metadata=metadata,
        resources=[],
        error=error,
    )


def to_skill_md(skill: Skill) -> str:
    """Serialise a :class:`Skill` to SKILL.md text (round-trips with the parser).

    ``license`` (and any future :data:`_TOP_LEVEL_META` key) is emitted at the
    frontmatter top level; everything else in ``metadata`` goes under the nested
    ``metadata:`` block. The body is the instructions.
    """
    meta = dict(skill.metadata or {})
    top: dict = {}
    for key in _TOP_LEVEL_META:
        if key in meta:
            top[key] = meta.pop(key)

    frontmatter: dict = {
        "name": skill.name or "",
        "description": skill.description or "",
    }
    frontmatter.update(top)
    if meta:
        frontmatter["metadata"] = meta

    fm_text = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).strip()
    body = (skill.instructions or "").strip("\n")
    return f"---\n{fm_text}\n---\n\n{body}\n"


# ── parse / serialise: JSON (home-grown programmatic form) ────────────────────


def skill_to_dict(skill: Skill) -> dict:
    """Serialise a :class:`Skill` to a plain JSON-friendly dict."""
    return {
        "name": skill.name,
        "description": skill.description,
        "instructions": skill.instructions,
        "metadata": dict(skill.metadata or {}),
        "resources": list(skill.resources or []),
    }


def skill_from_dict(data: Any) -> Skill:
    """Build a :class:`Skill` from the home-grown JSON form. NEVER raises."""
    if not isinstance(data, dict):
        return Skill(error=f"skill JSON is not an object: {type(data).__name__}")
    meta = data.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    resources = data.get("resources")
    if not isinstance(resources, list):
        resources = []
    return Skill(
        name=str(data.get("name") or "").strip(),
        description=str(data.get("description") or "").strip(),
        instructions=str(data.get("instructions") or ""),
        metadata=dict(meta),
        resources=[str(r) for r in resources],
    )


# ── a blank starter template ──────────────────────────────────────────────────


def template_skill(name: str, brief: str = "") -> Skill:
    """A minimal, ready-to-edit skill (used by ``skills create`` without ``--ai``).

    ``name`` is slugified; ``brief`` (if given) seeds the description and a TODO
    body so the file is immediately a valid SKILL.md the owner can flesh out.
    """
    slug = slugify(name) or "new-skill"
    description = (brief or "").strip() or f"TODO: one line describing when to use {slug}."
    instructions = (
        f"# {slug}\n\n"
        f"{description}\n\n"
        "## When to use\n\n"
        "TODO: describe the situations where this skill should load.\n\n"
        "## How to apply\n\n"
        "TODO: step-by-step instructions the brain follows when this skill is active.\n"
    )
    return Skill(
        name=slug,
        description=description,
        instructions=instructions,
        metadata={"source": "lhassa/template"},
        resources=[],
    )
