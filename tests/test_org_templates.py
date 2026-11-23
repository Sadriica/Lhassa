"""Tests for lhassa.org.templates; use-case org presets.

Offline: a tiny Config stand-in redirects the topology file + skills dir to a tmp
path (no real config/DB touched). Instantiation goes through the validated
management ops, so a bad template can never corrupt the saved chart.
"""

import pytest

from lhassa.org import templates
from lhassa.org import topology as topo
from lhassa.org.templates import TemplateError


class _Cfg:
    def __init__(self, tmp_path):
        self.org_role = ""  # standalone admin by default
        self.org_departments = ""
        self.org_topology_file = str(tmp_path / "org.json")
        self.root_admin_id = "lhassa"
        self.skills_dir = str(tmp_path / "skills")


@pytest.fixture
def cfg(tmp_path):
    return _Cfg(tmp_path)


# ── catalog ─────────────────────────────────────────────────────────────────────


def test_list_templates_has_software_factory():
    ids = {t["id"] for t in templates.list_templates()}
    assert {"code-review", "bug-investigation", "refactor-migration",
            "incident-response", "software-factory"} <= ids
    sf = next(t for t in templates.list_templates() if t["id"] == "software-factory")
    assert sf["brain_count"] == 5
    assert "frontend" in sf["skills"] or "backend" in sf["skills"]


def test_get_template_unknown_raises():
    with pytest.raises(TemplateError):
        templates.get_template("nope")


# ── instantiation ───────────────────────────────────────────────────────────────


def test_instantiate_software_factory_builds_under_admin(cfg):
    result = templates.instantiate("software-factory", config=cfg)
    chart = result["chart"]
    assert chart["root"] == "lhassa" and chart["admin"] == "lhassa"
    ids = {n["id"] for n in chart["nodes"]}
    assert "software-factory:delivery" in ids
    assert "software-factory:frontend" in ids
    assert len(result["created"]) == 5

    by_id = {n["id"]: n for n in chart["nodes"]}
    # delivery hangs under Lhassa (the permanent admin); departments under delivery.
    assert by_id["software-factory:delivery"]["parent"] == "lhassa"
    assert by_id["software-factory:frontend"]["parent"] == "software-factory:delivery"
    assert by_id["software-factory:devops"]["parent"] == "software-factory:delivery"
    # skills + limits carried through.
    assert "frontend" in by_id["software-factory:frontend"]["skills"]
    assert by_id["software-factory:devops"]["limits"].get("require_approval") is True


def test_instantiate_is_idempotent(cfg):
    first = templates.instantiate("code-review", config=cfg)
    assert len(first["created"]) == 2 and first["skipped"] == []
    second = templates.instantiate("code-review", config=cfg)
    assert second["created"] == [] and len(second["skipped"]) == 2
    # No duplicate brains.
    ids = [n["id"] for n in second["chart"]["nodes"]]
    assert len(ids) == len(set(ids))


def test_instantiate_unknown_template_raises(cfg):
    with pytest.raises(TemplateError):
        templates.instantiate("nope", config=cfg)


def test_instantiate_multi_level_hierarchy_valid(cfg):
    # incident-response: commander coordinator over triage/comms/remediation.
    result = templates.instantiate("incident-response", config=cfg)
    by_id = {n["id"]: n for n in result["chart"]["nodes"]}
    assert by_id["incident-response:commander"]["parent"] == "lhassa"
    for leaf in ("triage", "comms", "remediation"):
        assert by_id[f"incident-response:{leaf}"]["parent"] == "incident-response:commander"
    # The persisted chart re-loads as a valid topology (validates the tree).
    reloaded = topo.load_topology(cfg)
    assert reloaded.has("incident-response:commander")


# ── skills seeding (best effort) ────────────────────────────────────────────────


def test_ensure_template_skills_seeds_missing(cfg):
    created = templates.ensure_template_skills("code-review", cfg)
    assert "code_review" in created
    from lhassa.skills import SkillStore

    store = SkillStore(cfg)
    assert store.get("code_review") is not None
    # Idempotent: a second call seeds nothing.
    assert templates.ensure_template_skills("code-review", cfg) == []
