# SPDX-License-Identifier: AGPL-3.0-or-later
"""The :class:`SkillStore` over ``config.skills_dir`` + the org bridges.

A skill is a folder ``<skills_dir>/<name>/`` holding a ``SKILL.md`` (or a
home-grown ``skill.json``) plus any bundled resource files. The store is the
admin surface's persistence layer: list, get, create/save, delete, import an
external skill folder/file, and export one for sharing.

Two thin bridges connect skills to the org layer (read-only imports — this
module never edits :mod:`lhassa.org.management`):

* :func:`brains_with_skill` — which brains in the topology HAVE a skill (their
  :attr:`BrainNode.skills` contains it).
* :func:`assign_to_brain` — ADD a skill to a brain, reusing
  :func:`lhassa.org.management.set_skills` (so all topology validation/persistence
  is unchanged).

Reads degrade, never crash: an unreadable/empty file yields a :class:`Skill`
carrying a clear ``error`` (see :mod:`lhassa.skills.skillmd`).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from loguru import logger

from lhassa.registry import Registry
from lhassa.skills.skillmd import (
    Skill,
    parse_skill_md,
    skill_from_dict,
    slugify,
    to_skill_md,
)

if TYPE_CHECKING:
    from lhassa.config import Config

__all__ = [
    "SkillStore",
    "is_installed",
    "installed_names",
    "brains_with_skill",
    "assign_to_brain",
]

SKILL_MD = "SKILL.md"
SKILL_JSON = "skill.json"
# Files that ARE the skill definition (not listed as bundled resources).
_DEFINITION_FILES = {SKILL_MD, SKILL_JSON}


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def _skill_name(skill: Union[Skill, str]) -> str:
    """The string name of a skill, accepting either a :class:`Skill` or a name."""
    if isinstance(skill, Skill):
        return skill.name
    return str(skill or "").strip()


def _list_resources(folder: Path) -> list:
    """Filenames bundled alongside the definition in ``folder`` (sorted)."""
    out: list = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.name not in _DEFINITION_FILES:
            out.append(p.name)
    return out


def _read_skill_folder(folder: Path) -> Skill:
    """Read one skill folder (or a direct definition file). NEVER raises.

    Prefers ``SKILL.md``; falls back to ``skill.json``. The ``name`` defaults to
    the folder name when the frontmatter omits it, and bundled files are listed
    as resources.
    """
    if folder.is_file():
        # A direct file path: SKILL.md or skill.json.
        try:
            text = folder.read_text(encoding="utf-8")
        except Exception as exc:
            return Skill(name=folder.parent.name, error=f"unreadable: {exc}")
        if folder.name == SKILL_JSON or folder.suffix == ".json":
            try:
                skill = skill_from_dict(json.loads(text))
            except Exception as exc:
                skill = Skill(error=f"invalid JSON: {exc}")
        else:
            skill = parse_skill_md(text)
        name = skill.name or folder.parent.name
        return replace(skill, name=name)

    md = folder / SKILL_MD
    js = folder / SKILL_JSON
    if md.exists():
        try:
            skill = parse_skill_md(md.read_text(encoding="utf-8"))
        except Exception as exc:  # extremely defensive — parse_skill_md is safe
            skill = Skill(error=f"unreadable SKILL.md: {exc}")
    elif js.exists():
        try:
            skill = skill_from_dict(json.loads(js.read_text(encoding="utf-8")))
        except Exception as exc:
            skill = Skill(error=f"invalid skill.json: {exc}")
    else:
        return Skill(name=folder.name, error=f"no {SKILL_MD} in {folder}")

    return replace(
        skill,
        name=skill.name or folder.name,
        resources=_list_resources(folder),
    )


class SkillStore:
    """Filesystem-backed store of skills under ``config.skills_dir``."""

    def __init__(self, config: "Optional[Config]" = None):
        self._config = _resolve_config(config)

    # ── paths ──────────────────────────────────────────────────────────────────
    @property
    def root(self) -> Path:
        return Path(getattr(self._config, "skills_dir", "") or "")

    def folder_for(self, name: str) -> Path:
        """The folder for ``name``, ALWAYS inside the store.

        ``slugify`` returns "" for all-punctuation input (".." among them), and
        the old fallback to the raw name then produced ``root/".."`` — the
        store's PARENT. Since ``delete`` rmtree's whatever this returns, a skill
        named ".." removed ``~/.lhassa`` and the brain DB with it. The name is
        now flattened to a single path component and the result is re-checked
        against the root, so no input can address anything outside it.
        """
        raw = (name or "").strip()
        slug = slugify(raw)
        if not slug:
            # Keep a stable, collision-free stand-in for un-sluggable names
            # instead of trusting the raw text as a path component.
            import hashlib

            slug = "skill-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        folder = (self.root / slug).resolve()
        root = self.root.resolve()
        if folder == root or root not in folder.parents:
            raise ValueError(f"skill name {name!r} does not resolve inside the store")
        return folder

    def _inside(self, candidate: Path) -> bool:
        """True when ``candidate`` really lives under the store root."""
        try:
            resolved = candidate.resolve()
            return resolved != self.root.resolve() and self.root.resolve() in resolved.parents
        except Exception:
            return False

    # ── read ───────────────────────────────────────────────────────────────────
    def list_skills(self) -> list[Skill]:
        """Every skill in the store (folder order). Missing dir → empty list."""
        root = self.root
        if not root or not root.exists():
            return []
        out: list[Skill] = []
        for child in sorted(root.iterdir()):
            if child.is_dir():
                out.append(_read_skill_folder(child))
        return out

    def get(self, name: str) -> Optional[Skill]:
        """The skill named ``name``, or ``None`` if it is not in the store.

        This is the read path used at think time to load a skill's instructions
        into a brain's prompt (see ``org/executor.py``), so every successful read
        bumps the curator's usage telemetry (best-effort — see
        :mod:`lhassa.skills.curator`; never breaks a caller on failure).
        """
        folder = self.folder_for(name)
        if not folder.exists() or not folder.is_dir():
            # Tolerate an exact (un-slugified) folder name too.
            alt = self.root / name
            if self._inside(alt) and alt.exists() and alt.is_dir():
                folder = alt
            else:
                return None
        skill = _read_skill_folder(folder)
        try:
            from lhassa.skills import curator as _curator

            _curator.record_use(skill.name or name, config=self._config)
        except Exception as exc:  # telemetry must never break a caller
            logger.warning("skills: usage telemetry failed for {} — {}", name, exc)
        return skill

    def exists(self, name: str) -> bool:
        return self.get(name) is not None

    def index(self) -> Registry[Skill]:
        """A keystone :class:`Registry` view of the store (name → Skill).

        A fresh snapshot built from disk each call — handy for the API/dashboard
        and consistent with every other Lhassa swap point being registry-backed.
        """
        reg: Registry[Skill] = Registry("skill")
        for skill in self.list_skills():
            if skill.name:
                reg.register(skill.name, skill, override=True)
        return reg

    # ── write ──────────────────────────────────────────────────────────────────
    def save(self, skill: Skill) -> Path:
        """Write ``skill`` to ``<skills_dir>/<name>/SKILL.md`` (overwriting).

        Returns the skill folder. Raises :class:`ValueError` only when the skill
        has no usable name (a real programmer error, not a malformed-file case).
        """
        name = slugify(skill.name) or slugify(skill.description.split("\n")[0])
        if not name:
            raise ValueError("cannot save a skill with no name")
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / SKILL_MD).write_text(
            to_skill_md(replace(skill, name=name)), encoding="utf-8"
        )
        return folder

    def create(self, skill: Skill) -> Path:
        """Like :meth:`save` but refuses to overwrite an existing skill."""
        name = slugify(skill.name)
        if name and (self.root / name).exists():
            raise FileExistsError(f"skill {name!r} already exists")
        return self.save(skill)

    def delete(self, name: str) -> bool:
        """Remove a skill folder. Returns ``True`` if something was removed."""
        try:
            folder = self.folder_for(name)
        except ValueError:
            return False
        if not folder.exists():
            alt = self.root / name
            if self._inside(alt) and alt.exists():
                folder = alt
        # Belt and braces: never remove anything that is not under the root.
        if self._inside(folder) and folder.exists() and folder.is_dir():
            shutil.rmtree(folder)
            return True
        return False

    # ── import / export ────────────────────────────────────────────────────────
    def import_path(self, path: Union[str, Path]) -> Skill:
        """Read an external skill (a folder, a SKILL.md, or a skill.json) and SAVE it.

        Returns the imported (and now stored) :class:`Skill`. The source is never
        modified. A source with no recoverable name is rejected with a clear error
        skill (not saved).
        """
        src = Path(path)
        if not src.exists():
            return Skill(error=f"import path does not exist: {src}")
        skill = _read_skill_folder(src)
        if not (skill.name or "").strip():
            return replace(skill, error=f"cannot import: no skill name found in {src}")
        self.save(skill)
        # Copy any bundled resources alongside the saved definition.
        if src.is_dir():
            dest = self.folder_for(skill.name)
            for res in skill.resources:
                try:
                    shutil.copy2(src / res, dest / res)
                except Exception as exc:  # pragma: no cover - best effort
                    logger.warning("skills: could not copy resource {} — {}", res, exc)
        return replace(self.get(skill.name) or skill, error=None)

    def export(self, name: str, dest_dir: Union[str, Path, None] = None) -> Path:
        """Export a skill folder for sharing.

        With ``dest_dir`` the whole skill folder is copied to
        ``<dest_dir>/<name>/`` (created) and that path is returned. Without it the
        stored folder path itself is returned (nothing copied). Raises
        :class:`KeyError` if the skill does not exist.
        """
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"no skill named {name!r} to export")
        src = self.folder_for(skill.name)
        if not src.exists():
            src = self.root / name
        if dest_dir is None:
            return src
        dest = Path(dest_dir) / skill.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return dest


# ── installed-status helpers (the store dir is the source of truth) ───────────


def is_installed(name: str, config: "Optional[Config]" = None) -> bool:
    """Whether a skill named ``name`` is present in the store. Never raises.

    "Installed" means its folder + SKILL.md exist on disk — the precondition for the
    executor to load its instructions into a brain's prompt. An assigned skill NAME
    that is not installed does nothing until it is.
    """
    if not (name or "").strip():
        return False
    try:
        return SkillStore(config).exists(name)
    except Exception as exc:  # pragma: no cover - store reads are defensive already
        logger.warning("skills: is_installed could not read the store — {}", exc)
        return False


def installed_names(config: "Optional[Config]" = None) -> set[str]:
    """The set of skill names currently installed in the store. Never raises."""
    try:
        return {s.name for s in SkillStore(config).list_skills() if s.name}
    except Exception as exc:  # pragma: no cover - store reads are defensive already
        logger.warning("skills: installed_names could not read the store — {}", exc)
        return set()


# ── org bridges (read-only imports of the org layer) ──────────────────────────


def brains_with_skill(name: str, config: "Optional[Config]" = None) -> list[str]:
    """Brain ids whose :attr:`BrainNode.skills` contains ``name`` (topology order)."""
    from lhassa.org.topology import load_topology

    name = (name or "").strip()
    if not name:
        return []
    topology = load_topology(_resolve_config(config))
    return [n.id for n in topology.nodes() if name in (n.skills or ())]


def assign_to_brain(
    skill: Union[Skill, str],
    brain_id: str,
    config: "Optional[Config]" = None,
):
    """ADD ``skill`` to ``brain_id``'s skill set and persist the topology.

    Reuses :func:`lhassa.org.management.set_skills` (which replaces the set) by
    merging the new skill into the brain's CURRENT skills, so an assignment never
    drops the skills a brain already had. Returns the updated topology. Raises
    :class:`KeyError` for an unknown brain and :class:`ValueError` for an invalid
    op (both surfaced verbatim by the CLI/API).
    """
    from lhassa.org import management
    from lhassa.org.topology import load_topology

    name = _skill_name(skill)
    if not name:
        raise ValueError("cannot assign a skill with no name")
    cfg = _resolve_config(config)
    topology = load_topology(cfg)
    node = topology.node(brain_id)  # KeyError if absent
    current = list(node.skills or ())
    if name not in current:
        current.append(name)
    return management.set_skills(brain_id, current, config=cfg)
