"""Tests for the ``templo`` seed (lhassa.seeds.templo) — Lhassa's inner department.

Fully offline: a tiny Config stand-in redirects the topology file to a tmp path,
mirroring tests/test_cv_seed.py. The seed goes through the validated org
management API, so a bad op can never corrupt the saved topology file.
"""

import pytest

from lhassa import seeds
from lhassa.org.topology import load_topology
from lhassa.seeds import templo as templo_seed


class _Cfg:
    def __init__(self, tmp_path):
        self.org_role = ""
        self.org_departments = ""
        self.org_topology_file = str(tmp_path / "org.json")
        self.root_admin_id = "lhassa"


@pytest.fixture
def cfg(tmp_path):
    return _Cfg(tmp_path)


def test_registry_exposes_templo():
    names = seeds.SEEDS.names()
    assert "templo" in names
    listed = {s["name"]: s["description"] for s in seeds.list_seeds()}
    assert "templo" in listed and listed["templo"]


def test_apply_templo_creates_department_under_lhassa(cfg):
    summary = seeds.apply_seed("templo", cfg)

    assert summary["errors"] == []
    assert summary["created"] is True
    assert summary["existing"] is False

    top = load_topology(cfg)
    assert top.has("lhassa") and top.has("templo")
    node = top.node("templo")
    assert node.parent == "lhassa"  # a DIRECT child of Lhassa
    assert node.department == "templo"
    assert node.role == templo_seed.TEMPLO_ROLE
    assert set(templo_seed.TEMPLO_SKILLS) <= set(node.skills)
    assert top.is_leaf("templo")


def test_apply_templo_is_idempotent(cfg):
    first = seeds.apply_seed("templo", cfg)
    assert first["created"] is True

    before = {n.id for n in load_topology(cfg).nodes()}
    second = seeds.apply_seed("templo", cfg)
    assert second["created"] is False
    assert second["existing"] is True
    after = {n.id for n in load_topology(cfg).nodes()}
    assert after == before  # no duplicate brains


def test_templo_is_a_sibling_not_nested_under_interns_or_personal(cfg):
    # templo is Lhassa's OWN inner department — distinct from interns/personal.
    from lhassa.org import management

    management.add_brain("general", role="general", parent=None, config=cfg)
    management.add_brain(
        "interns", role="department", parent="general", department="interns", config=cfg
    )
    seeds.apply_seed("templo", cfg)

    top = load_topology(cfg)
    assert top.node("templo").parent == "lhassa"
    assert top.node("interns").parent == "general"
    assert "templo" not in {c.id for c in top.children_of("interns")}
