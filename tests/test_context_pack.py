"""Context packs (Wave B — "Inteligencia" plan). Fully offline, $0, no model calls.

Covers: pack generation (4 files + digest headers), VISION pulling a seeded
fact, MEMORY's TRIED/OPEN sections, freshness skip on unchanged inputs, staleness
on a new fact, an unknown project degrading to ``None``, the ARCHITECTURE code
scan detecting a manifest, and the launch-wiring pointer block in the prompt.
"""

import asyncio

import pytest

from lhassa.core import projects
from lhassa.harness import context_pack


@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.security import signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    asyncio.run(db.init_db())
    return db


class _Cfg:
    """Minimal config stand-in — only the attrs context_pack reads defensively."""

    def __init__(self, projects_dir, instance_id="inst_personal"):
        self.projects_dir = str(projects_dir)
        self.instance_id = instance_id


def _create(name, **kw):
    return asyncio.run(projects.create_project("inst_personal", name, **kw))


def test_unknown_project_returns_none(brain, tmp_path):
    cfg = _Cfg(tmp_path / "packs")
    out = asyncio.run(context_pack.ensure("nope", config=cfg))
    assert out is None


def test_pack_generated_with_four_files_and_digest_headers(brain, tmp_path):
    row = _create("lhassa", kind="generic")
    cfg = _Cfg(tmp_path / "packs")
    pack_dir = asyncio.run(context_pack.ensure(row["id"], config=cfg))
    assert pack_dir is not None and pack_dir.is_dir()
    for name in ("VISION.md", "ARCHITECTURE.md", "RULES.md", "MEMORY.md"):
        f = pack_dir / name
        assert f.is_file()
        first_line = f.read_text().splitlines()[0]
        assert first_line.startswith("<!-- lhassa:generated at=")
        assert "source_digest=" in first_line


def test_vision_includes_a_seeded_fact(brain, tmp_path):
    row = _create("lhassa", kind="generic")
    from lhassa.memory import facts as facts_mod

    asyncio.run(
        facts_mod.add_fact(
            "inst_personal",
            "dato",
            "Lhassa should become the owner's daily driver brain.",
            tags=["lhassa"],
        )
    )
    cfg = _Cfg(tmp_path / "packs")
    pack_dir = asyncio.run(context_pack.ensure(row["id"], config=cfg))
    vision = (pack_dir / "VISION.md").read_text()
    assert "daily driver brain" in vision


def test_memory_lists_tried_run_and_open_task(brain, tmp_path):
    row = _create("lhassa", kind="generic")
    import lhassa.core.db as db

    asyncio.run(
        db.record_run(
            {
                "instance_id": "inst_personal",
                "kind": "cli",
                "entity_id": projects.entity_tag(row["id"]),
                "status": "completed",
                "summary": "ran the test suite for lhassa",
                "started_at": "2026-07-20T10:00:00+00:00",
            }
        )
    )
    asyncio.run(
        db.add_task(
            "inst_personal",
            "fix_lint",
            "lhassa needs a lint pass",
            {"project": "lhassa"},
        )
    )
    cfg = _Cfg(tmp_path / "packs")
    pack_dir = asyncio.run(context_pack.ensure(row["id"], config=cfg))
    memory_md = (pack_dir / "MEMORY.md").read_text()
    assert "ran the test suite for lhassa" in memory_md
    assert "fix_lint" in memory_md


def test_second_ensure_skips_regeneration_when_unchanged(brain, tmp_path):
    row = _create("lhassa", kind="generic")
    cfg = _Cfg(tmp_path / "packs")
    pack_dir = asyncio.run(context_pack.ensure(row["id"], config=cfg))
    vision_path = pack_dir / "VISION.md"
    mtime_before = vision_path.stat().st_mtime_ns

    again = asyncio.run(context_pack.ensure(row["id"], config=cfg))
    assert again == pack_dir
    assert vision_path.stat().st_mtime_ns == mtime_before
    assert asyncio.run(context_pack.is_stale(row["id"], config=cfg)) is False


def test_adding_a_fact_makes_pack_stale_then_regenerates(brain, tmp_path):
    row = _create("lhassa", kind="generic")
    cfg = _Cfg(tmp_path / "packs")
    pack_dir = asyncio.run(context_pack.ensure(row["id"], config=cfg))
    vision_path = pack_dir / "VISION.md"
    mtime_before = vision_path.stat().st_mtime_ns

    from lhassa.memory import facts as facts_mod

    asyncio.run(
        facts_mod.add_fact(
            "inst_personal", "dato", "lhassa is going open source.", tags=["lhassa"]
        )
    )
    assert asyncio.run(context_pack.is_stale(row["id"], config=cfg)) is True

    asyncio.run(context_pack.ensure(row["id"], config=cfg))
    assert vision_path.stat().st_mtime_ns != mtime_before
    vision = vision_path.read_text()
    assert "going open source" in vision


def test_architecture_scan_detects_a_manifest(brain, tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'myrepo'\n")
    (repo / "README.md").write_text("# My Repo\n\nSome text.\n")
    (repo / "src").mkdir()
    row = _create("myrepo", kind="code", path=str(repo))
    cfg = _Cfg(tmp_path / "packs")
    pack_dir = asyncio.run(context_pack.ensure(row["id"], config=cfg))
    arch = (pack_dir / "ARCHITECTURE.md").read_text()
    assert "pyproject.toml" in arch
    assert "src/" in arch
    assert "My Repo" in arch


# ── launch wiring ──────────────────────────────────────────────────────────


def test_launch_wiring_carries_pack_pointer_when_project_matches(brain, tmp_path, monkeypatch):
    """POST /api/code resolves a project from the run's cwd and threads the pack
    pointer into the CLI prompt; with no matching project the prompt is untouched.
    """
    import lhassa.api.code_routes as code_routes
    import lhassa.config as lhassa_config
    from lhassa.agents import local_cli

    monkeypatch.setattr(lhassa_config.Config, "instance_id", "inst_personal", raising=False)
    monkeypatch.setattr(lhassa_config.Config, "agent_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(
        context_pack, "_pack_dir", lambda pid, cfg: tmp_path / "packs" / pid / "context"
    )

    _create("here", kind="generic", path=str(tmp_path))

    captured_prompts: list[str] = []

    async def _fake_invoke(self, run_id, run, task, cwd):
        captured_prompts.append(self._hint + local_cli.context_pack_hint(self.context_pack_dir) + task)
        return "ok"

    monkeypatch.setattr(local_cli.LocalCliAgent, "_invoke", _fake_invoke)
    monkeypatch.setattr(local_cli, "is_installed", lambda cli: True)
    monkeypatch.setattr(local_cli, "detect_ai_cli", lambda: "claude")

    async def _run():
        pack_dir = await code_routes._resolve_context_pack(tmp_path)
        agent = local_cli.make_agent("claude", context_pack_dir=pack_dir)
        result = await agent._invoke("r1", None, "do the thing", tmp_path)
        return result

    asyncio.run(_run())
    assert captured_prompts
    assert "Project context (read these first):" in captured_prompts[0]
    assert "VISION.md" in captured_prompts[0]


def test_launch_wiring_untouched_when_no_project_matches(brain, tmp_path, monkeypatch):
    import lhassa.api.code_routes as code_routes
    import lhassa.config as lhassa_config
    from lhassa.agents import local_cli

    monkeypatch.setattr(lhassa_config.Config, "instance_id", "inst_personal", raising=False)

    async def _run():
        pack_dir = await code_routes._resolve_context_pack(tmp_path / "nowhere")
        agent = local_cli.make_agent("claude", context_pack_dir=pack_dir)
        return local_cli.context_pack_hint(agent.context_pack_dir)

    hint = asyncio.run(_run())
    assert hint == ""
