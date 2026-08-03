"""Tests for the personal secretary seed and canonical briefing flows."""

from lhassa import seeds
from lhassa.flows.store import FlowStore
from lhassa.org.topology import load_topology
from lhassa.personal import secretary


class _Cfg:
    def __init__(self, tmp_path):
        self.org_role = ""
        self.org_departments = ""
        self.org_topology_file = str(tmp_path / "org.json")
        self.flows_file = str(tmp_path / "flows.json")
        self.root_admin_id = "lhassa"


def test_personal_seed_is_registered():
    assert "personal" in seeds.SEEDS.names()
    listed = {s["name"]: s["description"] for s in seeds.list_seeds()}
    assert listed["personal"]


def test_personal_seed_creates_department_and_flows(tmp_path):
    cfg = _Cfg(tmp_path)

    summary = seeds.apply_seed("personal", cfg)

    assert summary["errors"] == []
    assert summary["department"] == secretary.PERSONAL_DEPARTMENT_ID
    assert summary["department_created"] is True
    assert set(summary["flows_saved"]) == {
        secretary.MORNING_FLOW_ID,
        secretary.EVENING_FLOW_ID,
    }

    top = load_topology(cfg)
    personal = top.node(secretary.PERSONAL_DEPARTMENT_ID)
    assert personal.department == "personal"
    assert personal.parent == "lhassa"
    assert "personal" in personal.skills

    store = FlowStore(cfg)
    morning = store.get(secretary.MORNING_FLOW_ID)
    evening = store.get(secretary.EVENING_FLOW_ID)
    assert morning is not None and evening is not None
    morning.validate()
    evening.validate()
    assert morning.name == "Personal Morning Briefing"
    assert evening.name == "Personal Evening Briefing"
    assert [e.from_node for e in morning.edges] == ["trigger", "collect", "prioritize", "compose"]
    assert [e.to_node for e in evening.edges] == ["review", "carry", "compose", "output"]
    assert morning.node("collect").config["brain"] == "personal"
    assert evening.node("review").config["brain"] == "personal"


def test_personal_seed_is_idempotent(tmp_path):
    cfg = _Cfg(tmp_path)

    first = seeds.apply_seed("personal", cfg)
    second = seeds.apply_seed("personal", cfg)

    assert first["department_created"] is True
    assert second["department_created"] is False
    assert set(second["flows_saved"]) == {
        secretary.MORNING_FLOW_ID,
        secretary.EVENING_FLOW_ID,
    }
    top = load_topology(cfg)
    assert [n.id for n in top.nodes()].count("personal") == 1

