"""Tests for the first-class SKILLS system (slice M).

Offline + hermetic: the skill store and org topology are redirected to a tmp dir
via a lightweight stub config (``types.SimpleNamespace``), and the AI ops are
driven through an injected fake provider — no real model is ever called.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lhassa.skills import ai as ai_mod
from lhassa.skills.skillmd import (
    Skill,
    parse_skill_md,
    skill_from_dict,
    skill_to_dict,
    slugify,
    template_skill,
    to_skill_md,
)
from lhassa.skills.store import SkillStore, assign_to_brain, brains_with_skill


# ── helpers ──────────────────────────────────────────────────────────────────


def _cfg(tmp_path):
    """A stub config with a tmp skills_dir + org topology file."""
    return SimpleNamespace(
        skills_dir=str(tmp_path / "skills"),
        org_topology_file=str(tmp_path / "org.json"),
        org_role="",
        org_departments="",
    )


def _seed_topology(cfg):
    """A flat general + two department brains on disk."""
    path = Path(cfg.org_topology_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {"id": "general", "role": "general", "parent": None},
                {
                    "id": "dept:opt",
                    "role": "department",
                    "department": "optimizacion",
                    "parent": "general",
                    "skills": ["optimizacion"],
                },
                {
                    "id": "dept:fin",
                    "role": "department",
                    "department": "finanzas",
                    "parent": "general",
                },
            ]
        )
    )


class FakeProvider:
    """A ModelProvider whose ``complete`` returns a fixed text — no network."""

    name = "fake"
    zero_retention = True
    is_local = True

    def __init__(self, text: str):
        self._text = text
        self.calls: list = []

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        from lhassa.llm.provider import Completion

        self.calls.append({"messages": messages, "opts": opts})
        return Completion(text=self._text, model="fake")


class BrokenProvider:
    """A ModelProvider that always fails — exercises the degrade path."""

    name = "broken"
    zero_retention = False
    is_local = False

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        from lhassa.llm.provider import ProviderNotConfigured

        raise ProviderNotConfigured("no creds")


# ── SKILL.md round-trip ──────────────────────────────────────────────────────


def test_skill_md_round_trips():
    skill = Skill(
        name="code-review",
        description="Review a diff for correctness and risk.",
        instructions="# code-review\n\nDo the thing.\n\n## How\n\nCarefully.",
        metadata={
            "license": "MIT",
            "domain": "code-review",
            "tags": ["quality", "review"],
            "allowed-tools": ["read_file", "shell"],
            "level": 2,
            "source": "anthropics/code-review",
        },
    )
    text = to_skill_md(skill)
    assert text.startswith("---\n")
    # license rides at the frontmatter top level; the rest under metadata.
    assert "license: MIT" in text
    assert "metadata:" in text

    back = parse_skill_md(text)
    assert back.name == skill.name
    assert back.description == skill.description
    assert back.instructions == skill.instructions
    assert back.metadata == skill.metadata
    assert back.error is None


def test_json_form_round_trips():
    skill = Skill(
        name="research",
        description="Gather and synthesise.",
        instructions="Body.",
        metadata={"domain": "research", "tags": ["search"]},
    )
    d = skill_to_dict(skill)
    back = skill_from_dict(json.loads(json.dumps(d)))
    assert back.name == skill.name
    assert back.metadata == skill.metadata
    assert back.instructions == skill.instructions


def test_parse_malformed_never_raises():
    # Broken YAML frontmatter — must degrade with an error, not raise.
    bad = "---\nname: x\n  description: : [unclosed\n---\n\nbody"
    skill = parse_skill_md(bad)
    assert isinstance(skill, Skill)
    assert skill.error is not None


def test_parse_no_frontmatter_uses_body_as_instructions():
    skill = parse_skill_md("# just markdown\n\nno frontmatter here")
    assert "just markdown" in skill.instructions
    assert skill.error is not None  # flagged, but still loads


def test_slugify_and_template():
    assert slugify("Code Review!") == "code-review"
    t = template_skill("My Skill", "does a thing")
    assert t.name == "my-skill"
    assert t.description == "does a thing"
    assert "When to use" in t.instructions


# ── store ────────────────────────────────────────────────────────────────────


def test_store_create_get_list_delete(tmp_path):
    cfg = _cfg(tmp_path)
    store = SkillStore(cfg)
    assert store.list_skills() == []

    skill = Skill(name="testing", description="write tests", instructions="Body.")
    folder = store.create(skill)
    assert (folder / "SKILL.md").exists()

    got = store.get("testing")
    assert got is not None and got.name == "testing"
    assert [s.name for s in store.list_skills()] == ["testing"]

    # create refuses to overwrite; save overwrites.
    with pytest.raises(FileExistsError):
        store.create(skill)
    store.save(Skill(name="testing", description="changed", instructions="New."))
    assert store.get("testing").description == "changed"

    assert store.delete("testing") is True
    assert store.get("testing") is None
    assert store.delete("testing") is False


def test_store_index_is_registry(tmp_path):
    cfg = _cfg(tmp_path)
    store = SkillStore(cfg)
    store.save(Skill(name="a", description="d", instructions="b"))
    store.save(Skill(name="b", description="d", instructions="b"))
    idx = store.index()
    assert set(idx.names()) == {"a", "b"}
    assert idx.get("a").name == "a"


def test_store_import_and_export(tmp_path):
    cfg = _cfg(tmp_path)
    store = SkillStore(cfg)

    # An external skill folder with a bundled resource.
    ext = tmp_path / "external" / "imported-skill"
    ext.mkdir(parents=True)
    (ext / "SKILL.md").write_text(
        "---\nname: imported-skill\ndescription: from outside\n---\n\nBody."
    )
    (ext / "helper.txt").write_text("resource data")

    imported = store.import_path(ext)
    assert imported.error is None
    assert imported.name == "imported-skill"
    stored = store.get("imported-skill")
    assert "helper.txt" in stored.resources

    # Export to a destination dir makes a portable copy.
    dest = store.export("imported-skill", dest_dir=tmp_path / "out")
    assert (dest / "SKILL.md").exists()
    # Export without a dest returns the stored folder path.
    here = store.export("imported-skill")
    assert here.name == "imported-skill"


def test_import_missing_path_degrades(tmp_path):
    store = SkillStore(_cfg(tmp_path))
    skill = store.import_path(tmp_path / "nope")
    assert skill.error is not None


# ── org bridges ──────────────────────────────────────────────────────────────


def test_brains_with_skill_reads_topology(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_topology(cfg)
    assert brains_with_skill("optimizacion", config=cfg) == ["dept:opt"]
    assert brains_with_skill("nonexistent", config=cfg) == []


def test_assign_to_brain_merges(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_topology(cfg)
    updated = assign_to_brain("research", "dept:opt", config=cfg)
    node = updated.node("dept:opt")
    # existing skill kept, new skill added (merge, not replace)
    assert set(node.skills) == {"optimizacion", "research"}
    assert brains_with_skill("research", config=cfg) == ["dept:opt"]


def test_assign_to_unknown_brain_raises(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_topology(cfg)
    with pytest.raises(KeyError):
        assign_to_brain("research", "ghost", config=cfg)


# ── recommend (offline) + seed ───────────────────────────────────────────────


def test_recommend_offline_excludes_installed(tmp_path):
    cfg = _cfg(tmp_path)
    store = SkillStore(cfg)
    store.save(ai_mod.catalog_skill(ai_mod.CATALOG[0]))  # install "optimizacion"

    recs = ai_mod.recommend_skills(config=cfg)
    names = {r["name"] for r in recs}
    assert "optimizacion" not in names  # installed → not recommended
    assert "code-review" in names


def test_recommend_ranks_by_context(tmp_path):
    cfg = _cfg(tmp_path)
    recs = ai_mod.recommend_skills("I need a security audit of secrets", config=cfg)
    assert recs[0]["name"] == "security-audit"
    assert "matches" in recs[0]["reason"]


def test_ensure_seed_is_idempotent(tmp_path):
    cfg = _cfg(tmp_path)
    seeded = ai_mod.ensure_seed(cfg)
    assert set(seeded) == {"optimizacion", "documentation", "code-review"}
    # second call seeds nothing (already present)
    assert ai_mod.ensure_seed(cfg) == []
    store = SkillStore(cfg)
    assert store.get("optimizacion") is not None


# ── AI ops (injected provider; degrade without one) ──────────────────────────


@pytest.mark.asyncio
async def test_create_skill_with_ai_parses_model_output(tmp_path):
    cfg = _cfg(tmp_path)
    md = (
        "---\nname: drafted\ndescription: a drafted skill\n"
        "metadata:\n  domain: misc\n---\n\n# drafted\n\nInstructions here."
    )
    provider = FakeProvider(md)
    skill = await ai_mod.create_skill_with_ai("draft me a skill", config=cfg, provider=provider)
    assert skill.error is None
    assert skill.description == "a drafted skill"
    assert "Instructions here." in skill.instructions
    assert provider.calls  # the model was consulted


@pytest.mark.asyncio
async def test_create_skill_with_ai_strips_code_fence(tmp_path):
    cfg = _cfg(tmp_path)
    fenced = "```md\n---\nname: fenced\ndescription: d\n---\n\nBody.\n```"
    skill = await ai_mod.create_skill_with_ai("x", config=cfg, provider=FakeProvider(fenced))
    assert skill.name == "fenced"
    assert skill.instructions.strip() == "Body."


@pytest.mark.asyncio
async def test_create_skill_with_ai_degrades(tmp_path):
    cfg = _cfg(tmp_path)
    skill = await ai_mod.create_skill_with_ai(
        "a budget helper", config=cfg, provider=BrokenProvider()
    )
    # Degrades to a usable template carrying a clear error — never raises.
    assert skill.error is not None and skill.error.startswith("AI unavailable")
    assert "When to use" in skill.instructions


@pytest.mark.asyncio
async def test_improve_skill_with_ai(tmp_path):
    cfg = _cfg(tmp_path)
    store = SkillStore(cfg)
    store.save(Skill(name="rough", description="meh", instructions="terse"))

    better = "---\nname: rough\ndescription: much sharper\n---\n\nClear, actionable steps."
    improved = await ai_mod.improve_skill_with_ai(
        "rough", config=cfg, provider=FakeProvider(better)
    )
    assert improved.error is None
    assert improved.name == "rough"  # identity preserved
    assert improved.description == "much sharper"
    assert "Clear, actionable" in improved.instructions


@pytest.mark.asyncio
async def test_improve_missing_skill(tmp_path):
    cfg = _cfg(tmp_path)
    improved = await ai_mod.improve_skill_with_ai(
        "ghost", config=cfg, provider=FakeProvider("whatever")
    )
    assert improved.error is not None


@pytest.mark.asyncio
async def test_improve_degrades_keeps_original(tmp_path):
    cfg = _cfg(tmp_path)
    store = SkillStore(cfg)
    store.save(Skill(name="keep", description="orig", instructions="orig body"))
    improved = await ai_mod.improve_skill_with_ai(
        "keep", config=cfg, provider=BrokenProvider()
    )
    assert improved.error is not None
    assert improved.instructions == "orig body"  # unchanged
