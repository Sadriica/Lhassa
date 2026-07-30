"""Tests for the ``temple`` seed (lhassa.seeds.temple) — Lhassa's inner department.

Fully offline: a tiny Config stand-in redirects the topology file to a tmp path,
mirroring tests/test_cv_seed.py. The seed goes through the validated org
management API, so a bad op can never corrupt the saved topology file.
"""

import pytest

from lhassa import seeds
from lhassa.org.topology import load_topology
from lhassa.seeds import temple as temple_seed


class _Cfg:
    def __init__(self, tmp_path):
        self.org_role = ""
        self.org_departments = ""
        self.org_topology_file = str(tmp_path / "org.json")
        self.root_admin_id = "lhassa"


@pytest.fixture
def cfg(tmp_path):
    return _Cfg(tmp_path)


def test_registry_exposes_temple():
    names = seeds.SEEDS.names()
    assert "temple" in names
    listed = {s["name"]: s["description"] for s in seeds.list_seeds()}
    assert "temple" in listed and listed["temple"]


def test_apply_temple_creates_department_under_lhassa(cfg):
    summary = seeds.apply_seed("temple", cfg)

    assert summary["errors"] == []
    assert summary["created"] is True
    assert summary["existing"] is False

    top = load_topology(cfg)
    assert top.has("lhassa") and top.has("temple")
    node = top.node("temple")
    assert node.parent == "lhassa"  # a DIRECT child of Lhassa
    assert node.department == "temple"
    assert node.role == temple_seed.TEMPLE_ROLE
    assert set(temple_seed.TEMPLE_SKILLS) <= set(node.skills)
    assert not top.is_leaf("temple")  # it now has 5 worker brains under it


def test_apply_temple_creates_the_worker_brains(cfg):
    summary = seeds.apply_seed("temple", cfg)

    assert summary["errors"] == []
    assert set(summary["workers_created"]) == set(temple_seed.TEMPLE_WORKERS)
    assert summary["workers_existing"] == []

    top = load_topology(cfg)
    child_ids = {c.id for c in top.children_of("temple")}
    assert child_ids == set(temple_seed.TEMPLE_WORKERS)
    for worker_id in temple_seed.TEMPLE_WORKERS:
        node = top.node(worker_id)
        assert node.parent == "temple"
        assert node.department == "temple"
        assert node.role == temple_seed.TEMPLE_WORKER_ROLE
        assert top.is_leaf(worker_id)


def test_apply_temple_is_idempotent(cfg):
    first = seeds.apply_seed("temple", cfg)
    assert first["created"] is True
    assert set(first["workers_created"]) == set(temple_seed.TEMPLE_WORKERS)

    before = {n.id for n in load_topology(cfg).nodes()}
    second = seeds.apply_seed("temple", cfg)
    assert second["created"] is False
    assert second["existing"] is True
    assert second["workers_created"] == []
    assert set(second["workers_existing"]) == set(temple_seed.TEMPLE_WORKERS)
    after = {n.id for n in load_topology(cfg).nodes()}
    assert after == before  # no duplicate brains


def test_temple_is_a_sibling_not_nested_under_interns_or_personal(cfg):
    # temple is Lhassa's OWN inner department — distinct from interns/personal.
    from lhassa.org import management

    management.add_brain("general", role="general", parent=None, config=cfg)
    management.add_brain(
        "interns", role="department", parent="general", department="interns", config=cfg
    )
    seeds.apply_seed("temple", cfg)

    top = load_topology(cfg)
    assert top.node("temple").parent == "lhassa"
    assert top.node("interns").parent == "general"
    assert "temple" not in {c.id for c in top.children_of("interns")}
