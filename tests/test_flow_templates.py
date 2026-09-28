"""Tests for lhassa.flows.templates; the flow-templates gallery.

Offline: every template builds a VALID Flow with no store touched; instantiation
tests point ``config.flows_file`` at a tmp path (no real ``~/.lhassa`` touched). The
CLI tests use Typer's CliRunner against the real ``app``, the same pattern as
``tests/test_org_cmds.py``.
"""

import json

import pytest
from typer.testing import CliRunner

from lhassa.cli.app import app
from lhassa.flows import templates
from lhassa.flows.model import Flow
from lhassa.flows.store import FlowStore
from lhassa.flows.templates import FlowTemplateError

runner = CliRunner()


def _use_tmp(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    flows_file = tmp_path / "flows.json"
    monkeypatch.setattr(config_mod.Config, "flows_file", str(flows_file))
    return flows_file


# ── catalog ───────────────────────────────────────────────────────────────────────


def test_gallery_has_all_four_templates():
    ids = {t["id"] for t in templates.list_templates()}
    assert ids == {
        "research-brief",
        "code-review-pass",
        "daily-summary",
        "content-pipeline",
    }


def test_gallery_entries_have_description_and_teaches():
    for t in templates.list_templates():
        assert t["description"]
        assert t["teaches"]
        assert t["node_count"] >= 3
        assert t["node_kinds"][0] == "trigger"


def test_get_template_unknown_raises():
    with pytest.raises(FlowTemplateError):
        templates.get_template("nope")


# ── every template builds a VALID flow ───────────────────────────────────────────


@pytest.mark.parametrize(
    "template_id",
    ["research-brief", "code-review-pass", "daily-summary", "content-pipeline"],
)
def test_every_template_builds_a_valid_flow(template_id):
    flow = templates.build(template_id)
    assert isinstance(flow, Flow)
    flow.validate()  # no raise
    assert flow.trigger().kind.value == "trigger"


def test_research_brief_has_a_verify_gate():
    flow = templates.build("research-brief")
    kinds = [n.kind.value for n in flow.nodes]
    assert kinds == ["trigger", "task", "gate", "output"]
    gate = flow.node("checked")
    assert gate.config["type"] == "verify"
    assert gate.config["spec"]["type"] == "regex"


def test_code_review_pass_has_a_branch():
    flow = templates.build("code-review-pass")
    kinds = [n.kind.value for n in flow.nodes]
    assert "branch" in kinds
    branch_edges = flow.edges_from("verdict")
    assert len(branch_edges) == 2
    assert any(e.when is not None for e in branch_edges)
    assert any(e.when is None for e in branch_edges)  # the implicit "else"


def test_daily_summary_is_the_three_node_hello_world():
    flow = templates.build("daily-summary")
    assert len(flow.nodes) == 3
    assert [n.kind.value for n in flow.nodes] == ["trigger", "task", "output"]


def test_content_pipeline_has_an_approval_gate():
    flow = templates.build("content-pipeline")
    kinds = [n.kind.value for n in flow.nodes]
    assert kinds.count("task") == 2
    gate = flow.node("approval")
    assert gate.kind.value == "gate"
    assert gate.config["type"] == "approval"


def test_build_unknown_template_raises():
    with pytest.raises(FlowTemplateError):
        templates.build("nope")


# ── instantiation (apply_template) ───────────────────────────────────────────────


def test_apply_template_creates_it_in_the_store(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    import lhassa.config as config_mod

    config = config_mod.Config()
    flow = templates.apply_template("daily-summary", config=config)
    assert flow.id == "daily-summary"

    store = FlowStore(config)
    stored = store.get("daily-summary")
    assert stored is not None
    assert stored.name == "Daily Summary"


def test_reapply_does_not_clobber_gets_a_fresh_id(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    import lhassa.config as config_mod

    config = config_mod.Config()
    first = templates.apply_template("daily-summary", config=config)
    second = templates.apply_template("daily-summary", config=config)
    third = templates.apply_template("daily-summary", config=config)

    assert first.id == "daily-summary"
    assert second.id == "daily-summary-2"
    assert third.id == "daily-summary-3"

    store = FlowStore(config)
    ids = {f.id for f in store.list_flows()}
    assert {"daily-summary", "daily-summary-2", "daily-summary-3"} <= ids


def test_apply_template_never_overwrites_a_customized_flow(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    import lhassa.config as config_mod

    config = config_mod.Config()
    store = FlowStore(config)
    # The owner customized the flow under the template's default id.
    customized = templates.build("daily-summary")
    customized.name = "My Customized Daily Summary"
    store.create(customized)

    applied = templates.apply_template("daily-summary", config=config)
    assert applied.id == "daily-summary-2"

    still_there = store.get("daily-summary")
    assert still_there.name == "My Customized Daily Summary"


def test_apply_unknown_template_raises_before_any_write(tmp_path, monkeypatch):
    flows_file = _use_tmp(tmp_path, monkeypatch)
    import lhassa.config as config_mod

    config = config_mod.Config()
    with pytest.raises(FlowTemplateError):
        templates.apply_template("nope", config=config)
    assert not flows_file.exists()


# ── CLI (Typer CliRunner, same pattern as tests/test_org_cmds.py) ───────────────


def test_cli_flows_templates_lists_the_gallery(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(app, ["flows", "templates", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    ids = {t["id"] for t in data}
    assert "daily-summary" in ids


def test_cli_flows_apply_creates_and_prints_new_id(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(app, ["flows", "apply", "daily-summary", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["id"] == "daily-summary"


def test_cli_flows_apply_unknown_template_exits_nonzero(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(app, ["flows", "apply", "nope"])
    assert res.exit_code == 1


def test_cli_flows_list_and_show(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    runner.invoke(app, ["flows", "apply", "daily-summary"])

    res_list = runner.invoke(app, ["flows", "list", "--json"])
    assert res_list.exit_code == 0, res_list.output
    listed = json.loads(res_list.output)
    assert any(f["id"] == "daily-summary" for f in listed)

    res_show = runner.invoke(app, ["flows", "show", "daily-summary", "--json"])
    assert res_show.exit_code == 0, res_show.output
    shown = json.loads(res_show.output)
    assert shown["name"] == "Daily Summary"
    assert len(shown["nodes"]) == 3


def test_cli_flows_show_missing_flow_exits_nonzero(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(app, ["flows", "show", "ghost"])
    assert res.exit_code == 1
