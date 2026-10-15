# SPDX-License-Identifier: AGPL-3.0-or-later
"""Context packs; the rented body starts a run with the RIGHT context.

Every time Lhassa hands a task to a local AI CLI (claude/codex/opencode via
``/code`` or the API), the run should start knowing what it's working ON
instead of rederiving it from scratch. A context pack is four generated
markdown files, written NEXT TO the project's tracked state (never inside the
owner's repo; see ``doc/boundaries.md``), following the VISION/ARCHITECTURE/
RULES/MEMORY convention:

  - ``VISION.md``      : what the project is and what success looks like.
  - ``ARCHITECTURE.md``: a shallow, deterministic shape-of-the-code scan.
  - ``RULES.md``       : standing rules the body must honor.
  - ``MEMORY.md``      : the loop-engineering three: TRIED / VERIFIED / OPEN.

Everything here is **$0 and deterministic**: pure templating over local reads
(``core/projects``, ``memory/facts``, ``core/db``), no model call. Every public
function degrades to ``None``/``False`` rather than raising: a missing project,
an unreadable path, or a DB hiccup must never break a launch; context is a
gift, not a gate.

Freshness: each file carries a header comment
``<!-- lhassa:generated at=<iso> source_digest=<sha256-12> -->``. The digest is
derived from the project's ``updated_at``, the newest relevant fact timestamp,
and the newest relevant run's ``started_at``: cheap inputs that change exactly
when the pack's source material changes. :func:`ensure` re-reads the existing
header and skips regeneration when the digest still matches (cheap stale
check); a mismatch or a missing file regenerates all four.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from loguru import logger

from lhassa.core import db
from lhassa.core import projects as projects_mod

_FILES = ("VISION.md", "ARCHITECTURE.md", "RULES.md", "MEMORY.md")

_HEADER_RE = re.compile(r"source_digest=([0-9a-f]+)")

_MANIFESTS = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
    "Gemfile",
    "pom.xml",
    "composer.json",
)

_README_NAMES = ("README.md", "README.rst", "README.txt", "README")

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}

# Mirrors doc/architecture.md's spirit; the rented body's non-negotiables, in 5-6 lines.
BASE_RULES = (
    "Never commit secrets, credentials, or personal/owner data.",
    'No AI/Claude attribution anywhere (no Co-Authored-By, no "generated with").',
    "Follow this project's existing conventions and structure; don't restructure without being asked.",
    "Prefer the smallest correct change over a rewrite.",
    "Never fabricate results; degrade honestly on failure, don't guess.",
    "Ask before destructive or irreversible actions.",
)


def _resolve_config(config):
    if config is not None:
        return config
    from lhassa.config import Config

    return Config


def _instance_id(config) -> str:
    from lhassa.config import Config

    return getattr(config, "instance_id", None) or Config.instance_id


def _pack_dir(project_id: str, config) -> Path:
    """Where a project's context pack lives. NEVER inside the owner's repo."""
    override = getattr(config, "projects_dir", "") or ""
    if override:
        base = Path(override).expanduser()
    else:
        base = Path.home() / ".lhassa" / "projects"
    return base / project_id / "context"


def _project_terms(project: dict) -> set[str]:
    terms = {str(project.get("id", "")).strip().lower()}
    name = str(project.get("name", "")).strip().lower()
    if name:
        terms.add(name)
    return {t for t in terms if t}


def _fact_matches(fact, terms: set[str]) -> bool:
    tags = {str(t).strip().lower() for t in (fact.tags or [])}
    if tags & terms:
        return True
    content = fact.contenido.lower()
    return any(len(t) >= 3 and t in content for t in terms)


async def _load_relevant_facts(instance_id: str, project: dict) -> list:
    """Facts tagged with, or mentioning, this project. Never raises."""
    try:
        from lhassa.memory import facts as facts_mod

        all_facts = await facts_mod.load_facts(instance_id)
    except Exception:
        return []
    terms = _project_terms(project)
    return [f for f in all_facts if _fact_matches(f, terms)]


async def _load_relevant_runs(instance_id: str, project_id: str) -> list[dict]:
    """Recent runs against this project's entity tag. Never raises."""
    try:
        return await db.list_runs(
            instance_id, entity_id=projects_mod.entity_tag(project_id), limit=10
        )
    except Exception:
        return []


def _mentions(text: str, terms: set[str]) -> bool:
    lowered = (text or "").lower()
    return any(len(t) >= 3 and t in lowered for t in terms)


async def _load_open_items(instance_id: str, project: dict) -> tuple[list[dict], list[dict]]:
    """Pending tasks + approvals that touch this project, bounded. Never raises."""
    terms = _project_terms(project)
    tasks: list[dict] = []
    approvals: list[dict] = []
    try:
        rows = await db.list_tasks(instance_id, estado="pendiente", limit=200)
        for row in rows:
            haystack = f"{row.get('accion', '')} {row.get('params') or {}}"
            if _mentions(haystack, terms):
                tasks.append(row)
            if len(tasks) >= 5:
                break
    except Exception:
        pass
    try:
        rows = await db.load_pending_approvals(instance_id)
        for row in rows:
            payload = row.get("payload") or {}
            haystack = f"{row.get('tipo', '')} {payload}"
            if _mentions(haystack, terms):
                approvals.append(row)
            if len(approvals) >= 5:
                break
    except Exception:
        pass
    return tasks, approvals


def _compute_digest(project: dict, facts: list, runs: list[dict]) -> str:
    updated_at = str(project.get("updated_at") or "")
    fact_ts = max((f.actualizado_en for f in facts), default="")
    run_ts = max((r.get("started_at") or "" for r in runs), default="")
    raw = f"{updated_at}|{fact_ts}|{run_ts}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]


def _existing_digest(path: Path) -> Optional[str]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            first_line = fh.readline()
        match = _HEADER_RE.search(first_line)
        return match.group(1) if match else None
    except Exception:
        return None


def _is_fresh(pack_dir: Path, digest: str) -> bool:
    for name in _FILES:
        path = pack_dir / name
        if not path.exists() or _existing_digest(path) != digest:
            return False
    return True


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, body: str, digest: str, now: str) -> None:
    header = f"<!-- lhassa:generated at={now} source_digest={digest} -->\n\n"
    path.write_text(header + body, encoding="utf-8")


def _render_metadata(metadata: dict, limit: int = 20) -> list[str]:
    lines: list[str] = []
    for key in sorted(metadata)[:limit]:
        lines.append(f"- {key}: {metadata[key]}")
    return lines


def _scan_code_architecture(path_str: str) -> dict:
    """A shallow, bounded, deterministic scan; never raises, never slow."""
    result = {"top_level_dirs": [], "manifests": [], "readme_heading": ""}
    try:
        root = Path(path_str)
        if not root.is_dir():
            return result
        entries = sorted(p.name for p in root.iterdir())
        dirs = [
            name
            for name in entries
            if not name.startswith(".")
            and name not in _SKIP_DIRS
            and (root / name).is_dir()
        ]
        result["top_level_dirs"] = dirs[:30]
        result["manifests"] = [m for m in _MANIFESTS if (root / m).is_file()]
        for name in _README_NAMES:
            readme = root / name
            if readme.is_file():
                try:
                    for line in readme.read_text(errors="replace").splitlines():
                        stripped = line.strip()
                        if stripped.startswith("#"):
                            result["readme_heading"] = stripped.lstrip("#").strip()
                            break
                except Exception:
                    pass
                break
    except Exception:
        pass
    return result


def _vision_content(project: dict, facts: list) -> str:
    lines = [
        "# VISION",
        "",
        f"- Name: {project.get('name', '')}",
        f"- Kind: {project.get('kind', '')}",
        f"- Status: {project.get('status', '')}",
        f"- Path: {project.get('path') or '(none recorded)'}",
    ]
    metadata = project.get("metadata") or {}
    if metadata:
        lines += ["", "## Metadata", *_render_metadata(metadata)]
    lines += ["", "## What success looks like"]
    vision_facts = [f for f in facts if f.tipo == "dato"]
    if vision_facts:
        lines += [f"- {f.contenido}" for f in vision_facts[:10]]
    else:
        lines.append("The owner has not recorded a vision yet; ask, don't invent.")
    return "\n".join(lines) + "\n"


def _architecture_content(project: dict) -> str:
    lines = ["# ARCHITECTURE", ""]
    kind = project.get("kind") or "generic"
    path = project.get("path")
    if kind == "code" and path:
        scan = _scan_code_architecture(path)
        lines.append(f"- Path: {path}")
        if scan["readme_heading"]:
            lines.append(f"- README heading: {scan['readme_heading']}")
        lines += ["", "## Top-level directories"]
        if scan["top_level_dirs"]:
            lines += [f"- {d}/" for d in scan["top_level_dirs"]]
        else:
            lines.append("(none found, or the path is unreadable)")
        lines += ["", "## Detected manifests"]
        if scan["manifests"]:
            lines += [f"- {m}" for m in scan["manifests"]]
        else:
            lines.append("(none detected)")
    else:
        lines.append(f"- Kind: {kind}")
        lines.append(f"- Path: {path or '(none recorded)'}")
        metadata = project.get("metadata") or {}
        if metadata:
            lines += ["", "## Metadata", *_render_metadata(metadata)]
        lines += ["", "No architecture scan available for this project kind."]
    return "\n".join(lines) + "\n"


def _rules_content(facts: list) -> str:
    lines = ["# RULES", "", "## Base rules"]
    lines += [f"- {r}" for r in BASE_RULES]
    lines += ["", "## Project-specific directives"]
    directives = [f for f in facts if f.tipo == "directiva"]
    if directives:
        lines += [f"- {f.contenido}" for f in directives[:10]]
    else:
        lines.append("No project-specific directives recorded yet.")
    return "\n".join(lines) + "\n"


def _memory_content(
    facts: list, runs: list[dict], tasks: list[dict], approvals: list[dict]
) -> str:
    lines = ["# MEMORY", "", "## TRIED (recent runs)"]
    if runs:
        for row in runs[:10]:
            started = row.get("started_at") or "?"
            status = row.get("status") or "?"
            summary = (row.get("summary") or "").strip() or "(no summary)"
            lines.append(f"- {started} [{status}] {summary}")
    else:
        lines.append("No runs recorded yet for this project.")

    lines += ["", "## VERIFIED (confirmed facts)"]
    verified = [f for f in facts if f.tipo == "dato"]
    if verified:
        lines += [f"- {f.contenido}" for f in verified[:10]]
    else:
        lines.append("Nothing verified yet.")

    lines += ["", "## OPEN (pending work)"]
    open_items: list[str] = []
    for row in tasks[:5]:
        open_items.append(f"- task: {row.get('accion', '?')} ({row.get('estado', '?')})")
    for row in approvals[:5]:
        payload = row.get("payload") or {}
        accion = payload.get("accion") or row.get("tipo") or "?"
        open_items.append(f"- approval: {accion} (pending)")
    if open_items:
        lines += open_items
    else:
        lines.append("Nothing open right now.")
    return "\n".join(lines) + "\n"


async def _gather(project_id: str, config) -> Optional[tuple]:
    """Resolve the project + everything the pack needs. ``None`` on unknown project."""
    instance_id = _instance_id(config)
    project = await projects_mod.get_project(instance_id, project_id)
    if project is None:
        return None
    facts = await _load_relevant_facts(instance_id, project)
    runs = await _load_relevant_runs(instance_id, project_id)
    return project, facts, runs


async def ensure(project_id: str, *, config=None) -> Optional[Path]:
    """Ensure a fresh context pack exists for ``project_id``; return its dir.

    ``None`` for an unknown/empty project id, or on any internal failure;
    context is a gift, not a gate, so this never raises. When the pack already
    matches the current source digest, this is a cheap no-op (a header read per
    file, no regeneration).
    """
    try:
        project_id = (project_id or "").strip()
        if not project_id:
            return None
        cfg = _resolve_config(config)
        gathered = await _gather(project_id, cfg)
        if gathered is None:
            return None
        project, facts, runs = gathered
        digest = _compute_digest(project, facts, runs)
        pack_dir = _pack_dir(project_id, cfg)
        if _is_fresh(pack_dir, digest):
            return pack_dir

        instance_id = _instance_id(cfg)
        tasks, approvals = await _load_open_items(instance_id, project)

        pack_dir.mkdir(parents=True, exist_ok=True)
        now = _now_iso()
        _write(pack_dir / "VISION.md", _vision_content(project, facts), digest, now)
        _write(pack_dir / "ARCHITECTURE.md", _architecture_content(project), digest, now)
        _write(pack_dir / "RULES.md", _rules_content(facts), digest, now)
        _write(
            pack_dir / "MEMORY.md",
            _memory_content(facts, runs, tasks, approvals),
            digest,
            now,
        )
        return pack_dir
    except Exception as exc:
        logger.debug(f"[context_pack] ensure({project_id!r}) skipped: {exc}")
        return None


async def is_stale(project_id: str, config=None) -> bool:
    """True if a regeneration is due (missing pack, mismatched digest). Never raises."""
    try:
        project_id = (project_id or "").strip()
        if not project_id:
            return False
        cfg = _resolve_config(config)
        gathered = await _gather(project_id, cfg)
        if gathered is None:
            return False
        project, facts, runs = gathered
        digest = _compute_digest(project, facts, runs)
        pack_dir = _pack_dir(project_id, cfg)
        return not _is_fresh(pack_dir, digest)
    except Exception:
        return False
