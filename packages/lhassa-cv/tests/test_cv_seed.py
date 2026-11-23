"""Tests for the seed registry + the ``cv`` seed (lhassa.seeds / lhassa.cv.surfaces.seed).

Fully offline: a tiny Config stand-in redirects the topology/flows/skills files to a
tmp path and ``LHASSA_CV_DIR`` is monkeypatched, so nothing touches a real model, the
owner's live ``~/.lhassa``, or the network. The seed goes through the validated org
management API + the FlowStore, so a bad op can never corrupt the saved files.
"""

import pytest

from lhassa import seeds
from lhassa.flows.store import FlowStore
from lhassa.org.topology import load_topology
from lhassa.registry import NotRegistered
from lhassa.cv.surfaces import seed as cv_seed


class _Cfg:
    """A minimal Config stand-in pointing every seam at a tmp path."""

    def __init__(self, tmp_path):
        self.org_role = ""  # standalone admin by default (a truly fresh env)
        self.org_departments = ""
        self.org_topology_file = str(tmp_path / "org.json")
        self.flows_file = str(tmp_path / "flows.json")
        self.skills_dir = str(tmp_path / "skills")
        self.root_admin_id = "lhassa"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    # Point the CV file-I/O seam at a temp dir (never the owner's ~/.lhassa/cv).
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path / "cv"))
    return _Cfg(tmp_path)


# ── registry ──────────────────────────────────────────────────────────────────────


def test_registry_exposes_cv():
    names = seeds.SEEDS.names()
    assert "cv" in names
    listed = {s["name"]: s["description"] for s in seeds.list_seeds()}
    assert "cv" in listed and listed["cv"]  # non-empty description


def test_apply_unknown_seed_raises():
    with pytest.raises(NotRegistered):
        seeds.apply_seed("does-not-exist")


# ── the cv seed builds the whole pipeline on a fresh env ────────────────────────────


def test_apply_cv_creates_interns_dept_and_six_brains(cfg):
    summary = seeds.apply_seed("cv", cfg)

    assert summary["errors"] == []
    assert set(summary["brains_created"]) == set(cv_seed.CV_WORKER_BRAINS)
    assert summary["brains_existing"] == []
    assert summary["monitor"] == "interns"
    assert summary["flow_id"] == cv_seed.DEFAULT_CV_FLOW_ID

    top = load_topology(cfg)
    # interns is a department under general with the 6 workers as leaves under it.
    assert top.has("general") and top.has("interns")
    assert top.node("interns").parent == "general"
    children = {c.id for c in top.children_of("interns")}
    assert children == set(cv_seed.CV_WORKER_BRAINS)
    for b in cv_seed.CV_WORKER_BRAINS:
        assert top.is_leaf(b)
        assert top.node(b).department == "interns"


def test_apply_cv_wires_interns_as_monitor(cfg):
    seeds.apply_seed("cv", cfg)
    interns = load_topology(cfg).node("interns")
    assert interns.role == "monitor"
    assert "monitor" in interns.skills  # the monitor skill merged in
    assert interns.limits.get("description")  # the supervisor description stashed


def test_apply_cv_seeds_flow_with_contact_gate_and_tex_output(cfg):
    summary = seeds.apply_seed("cv", cfg)
    flow = FlowStore(cfg).get(summary["flow_id"])
    assert flow is not None
    flow.validate()  # the seeded flow is a valid DAG

    gate2 = flow.node("gate-2")
    assert gate2.config.get("spec", {}).get("type") == "contact_preserved"
    output = flow.node("output-1")
    assert output.config.get("type") == "tex"
    # the 6 task nodes pin the 6 worker brains (captured verbatim).
    pinned = {n.config.get("brain") for n in flow.nodes if n.kind == "task"}
    assert pinned == set(cv_seed.CV_WORKER_BRAINS)


# ── idempotency + non-destructive ───────────────────────────────────────────────────


def test_apply_cv_is_idempotent(cfg):
    seeds.apply_seed("cv", cfg)
    before = {n.id for n in load_topology(cfg).nodes()}

    second = seeds.apply_seed("cv", cfg)
    assert second["brains_created"] == []
    assert set(second["brains_existing"]) == set(cv_seed.CV_WORKER_BRAINS)

    after = {n.id for n in load_topology(cfg).nodes()}
    assert after == before  # no duplicate brains


def test_apply_cv_writes_placeholder_only_when_absent(cfg):
    from lhassa.cv import paths as cv_paths

    # Fresh env: no base CV → a placeholder is written.
    first = seeds.apply_seed("cv", cfg)
    assert first["base_cv"] == "placeholder"
    base = cv_paths.base_path()
    assert base.exists() and "documentclass" in base.read_text(encoding="utf-8")


def test_apply_cv_never_overwrites_an_existing_base_cv(cfg):
    from lhassa.cv import paths as cv_paths

    base = cv_paths.base_path()
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text("REAL OWNER CV. DO NOT TOUCH", encoding="utf-8")

    summary = seeds.apply_seed("cv", cfg)
    assert summary["base_cv"] == "kept"
    assert base.read_text(encoding="utf-8") == "REAL OWNER CV. DO NOT TOUCH"


def test_flow_id_override(cfg):
    summary = cv_seed.seed_cv_pipeline(cfg, flow_id="flow-custom")
    assert summary["flow_id"] == "flow-custom"
    assert FlowStore(cfg).get("flow-custom") is not None


# ── a drop-in seed registers + applies exactly like cv ──────────────────────────────


def test_dropin_seed_shows_up_in_list_and_applies(cfg):
    marker = {}

    @seeds.register_seed("test-dropin", "A drop-in test seed.")
    def _apply(config=None):
        marker["called"] = config
        return {"ok": True}

    try:
        assert "test-dropin" in seeds.SEEDS.names()
        assert any(s["name"] == "test-dropin" for s in seeds.list_seeds())
        result = seeds.apply_seed("test-dropin", cfg)
        assert result == {"ok": True}
        assert marker["called"] is cfg
    finally:
        seeds.SEEDS.unregister("test-dropin")  # keep the global registry clean
