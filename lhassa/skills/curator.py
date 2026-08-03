# SPDX-License-Identifier: AGPL-3.0-or-later
"""The skills curator (Fase 2) — usage telemetry + a deterministic $0 lifecycle.

A Hermes-style curator: every skill read for use is tallied in a sidecar
``<skills_dir>/.usage.json`` file (name -> ``{use_count, last_used_at, pinned,
state}``), and a weekly, deterministic sweep (:func:`run_transitions`, wired as
the ``curate_skills`` action) walks that telemetry to move unused skills
through ``active -> stale -> archived``. Pinned skills are exempt from every
transition. Nothing is ever deleted: an "archived" skill is moved (not
removed) into ``<skills_dir>/.archive/``, and the whole skills directory is
tarred into ``<skills_dir>/.backups/`` the moment before the first archive of
a run. No LLM is involved anywhere in this module.

Every public helper degrades rather than raises: a missing or corrupt
``.usage.json`` reads back as ``{}``, and a failed write is logged and
swallowed — telemetry must never break a think.
"""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from lhassa.config import Config

__all__ = [
    "STATE_ACTIVE",
    "STATE_STALE",
    "STATE_ARCHIVED",
    "record_use",
    "pin",
    "get_usage",
    "set_state",
    "run_transitions",
    "restore",
]

USAGE_FILE = ".usage.json"
BACKUPS_DIR = ".backups"
ARCHIVE_DIR = ".archive"

STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"
_STATES = (STATE_ACTIVE, STATE_STALE, STATE_ARCHIVED)

# Directories under skills_dir that are curator plumbing, never a skill itself.
_RESERVED_DIRS = (BACKUPS_DIR, ARCHIVE_DIR)


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def _skills_dir(config: "Optional[Config]" = None) -> Path:
    """The same skills directory :class:`SkillStore` uses — never hardcoded."""
    cfg = _resolve_config(config)
    return Path(getattr(cfg, "skills_dir", "") or "")


def _usage_path(config: "Optional[Config]" = None) -> Path:
    return _skills_dir(config) / USAGE_FILE


def _now_utc(now: Optional[datetime] = None) -> datetime:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now_iso(now: Optional[datetime] = None) -> str:
    return _now_utc(now).isoformat()


# ── sidecar telemetry read/write (atomic, degrade-never-raise) ────────────────


def _load_usage(config: "Optional[Config]" = None) -> dict:
    """The whole usage map. Missing/corrupt file degrades to ``{}`` — never raises."""
    path = _usage_path(config)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("skills.curator: corrupt usage file {} — {}", path, exc)
        return {}


def _save_usage(usage: dict, config: "Optional[Config]" = None) -> None:
    """Atomically replace the usage file (temp file + ``os.replace``). Never raises."""
    path = _usage_path(config)
    tmp_name: Optional[str] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".usage-", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(usage, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception as exc:
        logger.warning("skills.curator: could not save usage file {} — {}", path, exc)
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _entry(name: str, usage: dict) -> dict:
    """The (mutable, in-place) usage entry for ``name``, seeded with defaults."""
    e = usage.get(name)
    if not isinstance(e, dict):
        e = {}
    e.setdefault("use_count", 0)
    e.setdefault("last_used_at", None)
    e.setdefault("pinned", False)
    e.setdefault("state", STATE_ACTIVE)
    usage[name] = e
    return e


# ── public telemetry helpers ───────────────────────────────────────────────────


def record_use(
    name: str, config: "Optional[Config]" = None, now: Optional[datetime] = None
) -> None:
    """Bump the use counter for skill ``name`` and stamp ``last_used_at``.

    Best-effort: any failure is logged and swallowed, never raised — this is
    called from the hot think-time load path and must never break a think.
    """
    name = (name or "").strip()
    if not name:
        return
    try:
        usage = _load_usage(config)
        entry = _entry(name, usage)
        entry["use_count"] = int(entry.get("use_count") or 0) + 1
        entry["last_used_at"] = _now_iso(now)
        _save_usage(usage, config)
    except Exception as exc:  # pragma: no cover - defensive, telemetry never breaks a think
        logger.warning("skills.curator: record_use failed for {} — {}", name, exc)


def pin(name: str, value: bool = True, config: "Optional[Config]" = None) -> None:
    """Mark (or unmark) skill ``name`` as pinned — exempt from every transition."""
    name = (name or "").strip()
    if not name:
        return
    try:
        usage = _load_usage(config)
        entry = _entry(name, usage)
        entry["pinned"] = bool(value)
        _save_usage(usage, config)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("skills.curator: pin failed for {} — {}", name, exc)


def get_usage(config: "Optional[Config]" = None) -> dict:
    """The whole usage telemetry map (name -> entry). Never raises."""
    return _load_usage(config)


def set_state(name: str, state: str, config: "Optional[Config]" = None) -> None:
    """Force skill ``name``'s lifecycle state. Raises on an unknown ``state``
    (a programmer error, not a malformed-data case)."""
    if state not in _STATES:
        raise RuntimeError(f"unknown skill curator state: {state!r}")
    name = (name or "").strip()
    if not name:
        return
    try:
        usage = _load_usage(config)
        entry = _entry(name, usage)
        entry["state"] = state
        _save_usage(usage, config)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("skills.curator: set_state failed for {} — {}", name, exc)


# ── lifecycle sweep ─────────────────────────────────────────────────────────


def _backup_skills_dir(skills_dir: Path, now: datetime) -> Path:
    """Tar the whole skills dir (minus curator plumbing) into ``.backups/``.

    Timestamped from the already-resolved ``now`` — callers must not call
    ``datetime.now`` again so a single run produces one consistent stamp.
    """
    backups_dir = skills_dir / BACKUPS_DIR
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    dest = backups_dir / f"skills-{stamp}.tar.gz"
    with tarfile.open(dest, "w:gz") as tar:
        for child in sorted(skills_dir.iterdir()):
            if child.name == BACKUPS_DIR:
                continue
            tar.add(child, arcname=child.name)
    return dest


def _iter_skill_dirs(skills_dir: Path) -> list:
    out = []
    for p in sorted(skills_dir.iterdir()):
        if not p.is_dir():
            continue
        if p.name in _RESERVED_DIRS or p.name.startswith("."):
            continue
        out.append(p)
    return out


def run_transitions(
    config: "Optional[Config]" = None, now: Optional[datetime] = None
) -> dict:
    """Deterministic, $0 sweep: active -> stale -> archived. NEVER deletes.

    A skill with no use for ``config.skill_stale_days`` (default 30) becomes
    "stale"; stale AND unused for ``config.skill_archive_days`` (default 90)
    is archived — first a tar.gz backup of the whole skills dir is written
    under ``.backups/`` (once per run, only if something is actually
    archived), then the skill's folder is MOVED (never removed) into
    ``.archive/``. Pinned skills are exempt from every transition. A skill
    with no recorded usage falls back to its folder's mtime as the baseline.

    ``now`` is accepted for testability — resolved exactly once so a run is
    internally consistent (the backup stamp and every age comparison agree).

    Returns ``{"checked", "stale", "archived", "skipped_pinned"}``. Never raises.
    """
    summary = {"checked": 0, "stale": 0, "archived": 0, "skipped_pinned": 0}
    try:
        cfg = _resolve_config(config)
        skills_dir = _skills_dir(cfg)
        if not skills_dir.exists():
            return summary

        now_dt = _now_utc(now)
        stale_days = int(getattr(cfg, "skill_stale_days", 30) or 30)
        archive_days = int(getattr(cfg, "skill_archive_days", 90) or 90)

        usage = _load_usage(cfg)
        backup_path: list = [None]  # lazy, at most one backup per run

        def _ensure_backup() -> None:
            if backup_path[0] is None:
                try:
                    backup_path[0] = _backup_skills_dir(skills_dir, now_dt)
                except Exception as exc:
                    logger.warning("skills.curator: backup failed — {}", exc)

        for folder in _iter_skill_dirs(skills_dir):
            name = folder.name
            summary["checked"] += 1

            existing = usage.get(name)
            pinned = bool(existing.get("pinned")) if isinstance(existing, dict) else False
            if pinned:
                summary["skipped_pinned"] += 1
                continue

            last_used: Optional[datetime] = None
            if isinstance(existing, dict) and existing.get("last_used_at"):
                try:
                    last_used = datetime.fromisoformat(existing["last_used_at"])
                except Exception:
                    last_used = None
            if last_used is None:
                try:
                    last_used = datetime.fromtimestamp(folder.stat().st_mtime, tz=timezone.utc)
                except Exception:
                    last_used = now_dt
            if last_used.tzinfo is None:
                last_used = last_used.replace(tzinfo=timezone.utc)

            age_days = (now_dt - last_used).total_seconds() / 86400.0
            entry = _entry(name, usage)

            if age_days >= archive_days:
                try:
                    _ensure_backup()
                    archive_root = skills_dir / ARCHIVE_DIR
                    archive_root.mkdir(parents=True, exist_ok=True)
                    dest = archive_root / name
                    if dest.exists():
                        dest = archive_root / f"{name}-{now_dt.strftime('%Y%m%dT%H%M%SZ')}"
                    shutil.move(str(folder), str(dest))
                    entry["state"] = STATE_ARCHIVED
                    summary["archived"] += 1
                except Exception as exc:
                    logger.warning("skills.curator: could not archive {} — {}", name, exc)
            elif age_days >= stale_days:
                entry["state"] = STATE_STALE
                summary["stale"] += 1
            else:
                entry["state"] = STATE_ACTIVE

        _save_usage(usage, cfg)
        return summary
    except Exception as exc:  # pragma: no cover - the sweep must never crash a caller
        logger.warning("skills.curator: run_transitions failed — {}", exc)
        return summary


def restore(name: str, config: "Optional[Config]" = None) -> bool:
    """Move an archived skill back from ``.archive/`` and reset it to active.

    Returns ``True`` on success, ``False`` if there is nothing to restore (no
    archived folder) or a live skill of the same name already exists (never
    overwrites). Never raises.
    """
    name = (name or "").strip()
    if not name:
        return False
    try:
        cfg = _resolve_config(config)
        skills_dir = _skills_dir(cfg)
        src = skills_dir / ARCHIVE_DIR / name
        if not src.exists():
            return False
        dest = skills_dir / name
        if dest.exists():
            return False
        shutil.move(str(src), str(dest))
        usage = _load_usage(cfg)
        entry = _entry(name, usage)
        entry["state"] = STATE_ACTIVE
        _save_usage(usage, cfg)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("skills.curator: restore failed for {} — {}", name, exc)
        return False
