"""Tests for the skills curator (Fase 2); usage telemetry + $0 lifecycle sweep.

Offline + hermetic: a tmp ``skills_dir`` stub config (same idiom as
``tests/test_skills.py``), no real model, no real agent runs.
"""

from __future__ import annotations

import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from lhassa.skills import curator
from lhassa.skills.skillmd import Skill
from lhassa.skills.store import SkillStore


def _cfg(tmp_path, **overrides):
    base = dict(
        skills_dir=str(tmp_path / "skills"),
        skill_stale_days=30,
        skill_archive_days=90,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _touch_skill(cfg, name: str, mtime: "datetime | None" = None) -> Path:
    store = SkillStore(cfg)
    folder = store.save(Skill(name=name, description="d", instructions="body"))
    if mtime is not None:
        ts = mtime.timestamp()
        import os

        os.utime(folder, (ts, ts))
        os.utime(folder / "SKILL.md", (ts, ts))
    return folder


# ── telemetry roundtrip ───────────────────────────────────────────────────────


def test_record_use_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    _touch_skill(cfg, "testing")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    curator.record_use("testing", config=cfg, now=now)
    curator.record_use("testing", config=cfg, now=now)

    usage = curator.get_usage(cfg)
    assert usage["testing"]["use_count"] == 2
    assert usage["testing"]["last_used_at"] == now.isoformat()
    assert usage["testing"]["pinned"] is False
    assert usage["testing"]["state"] == curator.STATE_ACTIVE


def test_record_use_empty_name_is_noop(tmp_path):
    cfg = _cfg(tmp_path)
    curator.record_use("", config=cfg)
    curator.record_use("   ", config=cfg)
    assert curator.get_usage(cfg) == {}


def test_pin_and_get_usage(tmp_path):
    cfg = _cfg(tmp_path)
    _touch_skill(cfg, "pinned-one")
    curator.pin("pinned-one", True, config=cfg)
    assert curator.get_usage(cfg)["pinned-one"]["pinned"] is True
    curator.pin("pinned-one", False, config=cfg)
    assert curator.get_usage(cfg)["pinned-one"]["pinned"] is False


def test_set_state_valid_and_invalid(tmp_path):
    cfg = _cfg(tmp_path)
    _touch_skill(cfg, "s")
    curator.set_state("s", curator.STATE_STALE, config=cfg)
    assert curator.get_usage(cfg)["s"]["state"] == curator.STATE_STALE

    with pytest.raises(RuntimeError):
        curator.set_state("s", "not-a-real-state", config=cfg)


def test_corrupt_usage_file_degrades(tmp_path):
    cfg = _cfg(tmp_path)
    skills_dir = Path(cfg.skills_dir)
    skills_dir.mkdir(parents=True)
    (skills_dir / curator.USAGE_FILE).write_text("{ not json ")

    assert curator.get_usage(cfg) == {}
    # record_use must still work; it degrades the read then overwrites cleanly.
    curator.record_use("x", config=cfg)
    assert curator.get_usage(cfg)["x"]["use_count"] == 1


def test_missing_usage_file_degrades(tmp_path):
    cfg = _cfg(tmp_path)
    assert curator.get_usage(cfg) == {}


# ── SkillStore.get() hooks record_use (best-effort, never breaks a read) ──────


def test_store_get_bumps_usage(tmp_path):
    cfg = _cfg(tmp_path)
    _touch_skill(cfg, "hooked")
    store = SkillStore(cfg)

    assert curator.get_usage(cfg) == {}
    skill = store.get("hooked")
    assert skill is not None and skill.name == "hooked"

    usage = curator.get_usage(cfg)
    assert usage["hooked"]["use_count"] == 1
    store.get("hooked")
    assert curator.get_usage(cfg)["hooked"]["use_count"] == 2


def test_store_get_missing_skill_does_not_record(tmp_path):
    cfg = _cfg(tmp_path)
    store = SkillStore(cfg)
    assert store.get("ghost") is None
    assert curator.get_usage(cfg) == {}


# ── run_transitions ───────────────────────────────────────────────────────────


def test_fresh_skill_stays_active(tmp_path):
    cfg = _cfg(tmp_path)
    _touch_skill(cfg, "fresh")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    summary = curator.run_transitions(cfg, now=now)
    assert summary == {"checked": 1, "stale": 0, "archived": 0, "skipped_pinned": 0}
    assert curator.get_usage(cfg)["fresh"]["state"] == curator.STATE_ACTIVE
    # still on disk, not archived
    assert (Path(cfg.skills_dir) / "fresh").exists()


def test_unused_31_days_goes_stale(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _touch_skill(cfg, "aging", mtime=now - timedelta(days=31))

    summary = curator.run_transitions(cfg, now=now)
    assert summary["stale"] == 1
    assert summary["archived"] == 0
    assert curator.get_usage(cfg)["aging"]["state"] == curator.STATE_STALE
    assert (Path(cfg.skills_dir) / "aging").exists()  # not moved


def test_unused_91_days_archives_with_backup(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _touch_skill(cfg, "ancient", mtime=now - timedelta(days=91))

    summary = curator.run_transitions(cfg, now=now)
    assert summary["archived"] == 1
    assert summary["stale"] == 0

    skills_dir = Path(cfg.skills_dir)
    # never deleted; moved into .archive/
    assert not (skills_dir / "ancient").exists()
    archived = skills_dir / curator.ARCHIVE_DIR / "ancient"
    assert archived.exists()
    assert (archived / "SKILL.md").exists()

    # a backup tarball exists and contains the archived skill
    backups = list((skills_dir / curator.BACKUPS_DIR).glob("*.tar.gz"))
    assert len(backups) == 1
    with tarfile.open(backups[0]) as tar:
        names = tar.getnames()
    assert any("ancient" in n for n in names)

    assert curator.get_usage(cfg)["ancient"]["state"] == curator.STATE_ARCHIVED


def test_pinned_skill_exempt_from_all_transitions(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _touch_skill(cfg, "forever", mtime=now - timedelta(days=365))
    curator.pin("forever", True, config=cfg)

    summary = curator.run_transitions(cfg, now=now)
    assert summary["skipped_pinned"] == 1
    assert summary["archived"] == 0
    assert summary["stale"] == 0
    assert (Path(cfg.skills_dir) / "forever").exists()
    assert curator.get_usage(cfg)["forever"]["state"] == curator.STATE_ACTIVE


def test_no_skills_dir_degrades(tmp_path):
    cfg = _cfg(tmp_path)  # skills_dir never created
    summary = curator.run_transitions(cfg, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert summary == {"checked": 0, "stale": 0, "archived": 0, "skipped_pinned": 0}


def test_recorded_use_resets_the_baseline(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _touch_skill(cfg, "recent-use", mtime=now - timedelta(days=200))
    # used just yesterday, despite an old mtime -> last_used_at wins
    curator.record_use("recent-use", config=cfg, now=now - timedelta(days=1))

    summary = curator.run_transitions(cfg, now=now)
    assert summary["archived"] == 0
    assert summary["stale"] == 0
    assert curator.get_usage(cfg)["recent-use"]["state"] == curator.STATE_ACTIVE


# ── restore ────────────────────────────────────────────────────────────────────


def test_restore_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _touch_skill(cfg, "comeback", mtime=now - timedelta(days=91))
    curator.run_transitions(cfg, now=now)

    skills_dir = Path(cfg.skills_dir)
    assert not (skills_dir / "comeback").exists()

    assert curator.restore("comeback", config=cfg) is True
    assert (skills_dir / "comeback").exists()
    assert (skills_dir / "comeback" / "SKILL.md").exists()
    assert curator.get_usage(cfg)["comeback"]["state"] == curator.STATE_ACTIVE


def test_restore_missing_returns_false(tmp_path):
    cfg = _cfg(tmp_path)
    assert curator.restore("nope", config=cfg) is False


def test_restore_never_overwrites_live_skill(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _touch_skill(cfg, "dup", mtime=now - timedelta(days=91))
    curator.run_transitions(cfg, now=now)
    # a brand-new "dup" was created again after the archive
    _touch_skill(cfg, "dup")

    assert curator.restore("dup", config=cfg) is False
    archived = Path(cfg.skills_dir) / curator.ARCHIVE_DIR / "dup"
    assert archived.exists()  # untouched, still safely archived


# ── the curate_skills action ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_curate_skills_action_runs_and_reports(tmp_path, monkeypatch):
    from lhassa.actionpack import _curate

    cfg = _cfg(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _touch_skill(cfg, "fresh")
    _touch_skill(cfg, "old-one", mtime=now - timedelta(days=91))

    monkeypatch.setattr(_curate, "Config", lambda: cfg)
    monkeypatch.setattr(_curate._watcher, "is_active", lambda *a, **k: False)

    result = await _curate.curate_skills({})
    assert result.startswith("curate_skills: checked 2")
    assert "archived 1" in result


@pytest.mark.asyncio
async def test_curate_skills_action_skips_when_agent_run_active(tmp_path, monkeypatch):
    from lhassa.actionpack import _curate
    from lhassa.state import AgentRun, state

    cfg = _cfg(tmp_path)
    _touch_skill(cfg, "irrelevant")
    monkeypatch.setattr(_curate, "Config", lambda: cfg)
    monkeypatch.setattr(
        state,
        "agent_runs",
        {"run-1": AgentRun(id="run-1", name="x", status="running")},
    )

    result = await _curate.curate_skills({})
    assert result == "curate_skills: skipped; agent run active"
    # nothing was touched; the sweep never ran
    assert curator.get_usage(cfg) == {}


@pytest.mark.asyncio
async def test_curate_skills_action_never_raises(tmp_path, monkeypatch):
    from lhassa.actionpack import _curate

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(_curate._watcher, "is_active", _boom)
    result = await _curate.curate_skills({})
    assert result.startswith("curate_skills: error:")
