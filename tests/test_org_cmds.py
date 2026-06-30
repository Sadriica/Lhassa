"""Tests for the `lhassa org` management sub-app (Fase 7, slice E).

Offline: the topology file is redirected to a tmp path via the Config class attr,
the brain DB to a tmp file, and the chat path's ``_run_sync`` is mocked so the
default prompt path is exercised without a real LLM call.
"""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from lhassa.cli.agent import CliResult
from lhassa.cli.app import app

runner = CliRunner()


def _use_tmp(tmp_path, monkeypatch):
    """Point the topology file + brain DB at the temp dir.

    Patches the LIVE ``lhassa.config.Config`` class (resolved through the module at
    call time) so the override survives ``test_config.py``'s ``importlib.reload`` —
    ``org_cmds`` imports ``Config`` freshly on each invocation, so a stale top-level
    binding would point at the wrong class object after a reload.
    """
    import lhassa.config as config_mod
    import lhassa.core.db as db

    topo_file = tmp_path / "org.json"
    monkeypatch.setattr(config_mod.Config, "org_topology_file", str(topo_file))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    return topo_file


def _seed_flat(topo_file):
    """A flat general + two departments chart on disk."""
    topo_file.write_text(
        json.dumps(
            [
                {"id": "general", "role": "general", "department": None, "parent": None},
                {
                    "id": "dept:optimization",
                    "role": "department",
                    "department": "optimization",
                    "parent": "general",
                },
                {
                    "id": "dept:finance",
                    "role": "department",
                    "department": "finance",
                    "parent": "general",
                },
            ]
        )
    )


# ── tree / list ─────────────────────────────────────────────────────────────────


def test_tree_json(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(app, ["org", "tree", "--json"])
    assert res.exit_code == 0
    rows = json.loads(res.output)
    # Lhassa (the permanent administrator) is injected at the root on load.
    assert {r["id"] for r in rows} == {
        "lhassa", "general", "dept:optimization", "dept:finance",
    }


def test_tree_pretty_runs(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(app, ["org", "tree"])
    assert res.exit_code == 0
    assert "general" in res.output


def test_list_json(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(app, ["org", "list", "--json"])
    assert res.exit_code == 0
    rows = json.loads(res.output)
    assert any(r["id"] == "dept:finance" and r["department"] == "finance" for r in rows)


def test_default_derivation_when_no_file(tmp_path, monkeypatch):
    # No topology file written → derived standalone single brain = Lhassa (admin).
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(app, ["org", "list", "--json"])
    assert res.exit_code == 0
    rows = json.loads(res.output)
    assert len(rows) == 1
    assert rows[0]["id"] == "lhassa"
    assert rows[0]["role"] == "administrator"


# ── add ─────────────────────────────────────────────────────────────────────────


def test_add_persists(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(
        app,
        [
            "org", "add",
            "--id", "dept:documentation",
            "--role", "department",
            "--parent", "general",
            "--department", "documentation",
            "--json",
        ],
    )
    assert res.exit_code == 0
    assert json.loads(res.output)["id"] == "dept:documentation"
    # persisted to disk
    ids = {n["id"] for n in json.loads(topo_file.read_text())}
    assert "dept:documentation" in ids


def test_add_into_empty_creates_hierarchy(tmp_path, monkeypatch):
    # Fresh (no file) → standalone Lhassa; adding a brain with no parent attaches it
    # UNDER the administrator (you can never create a second root).
    topo_file = _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(
        app,
        ["org", "add", "--id", "dept:x", "--department", "x"],
    )
    assert res.exit_code == 0
    persisted = {n["id"]: n for n in json.loads(topo_file.read_text())}
    assert set(persisted) == {"lhassa", "dept:x"}
    assert persisted["dept:x"]["parent"] == "lhassa"


def test_add_invalid_parent_fails(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(
        app, ["org", "add", "--id", "dept:y", "--parent", "ghost"]
    )
    assert res.exit_code == 1


# ── remove ──────────────────────────────────────────────────────────────────────


def test_remove_leaf(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(app, ["org", "remove", "--id", "dept:finance", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["removed"] == ["dept:finance"]
    ids = {n["id"] for n in json.loads(topo_file.read_text())}
    assert "dept:finance" not in ids


def test_remove_root_with_children_refused(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(app, ["org", "remove", "--id", "general"])
    assert res.exit_code == 1
    # unchanged on disk
    ids = {n["id"] for n in json.loads(topo_file.read_text())}
    assert "general" in ids


def test_remove_subtree_with_force(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    topo_file.write_text(
        json.dumps(
            [
                {"id": "general", "role": "general", "department": None, "parent": None},
                {"id": "ops", "role": "sub_general", "department": None, "parent": "general"},
                {
                    "id": "dept:optimization",
                    "role": "department",
                    "department": "optimization",
                    "parent": "ops",
                },
            ]
        )
    )
    res = runner.invoke(app, ["org", "remove", "--id", "ops", "--force", "--json"])
    assert res.exit_code == 0
    removed = set(json.loads(res.output)["removed"])
    assert removed == {"ops", "dept:optimization"}
    ids = {n["id"] for n in json.loads(topo_file.read_text())}
    assert ids == {"lhassa", "general"}


def test_remove_missing_fails(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(app, ["org", "remove", "--id", "nope"])
    assert res.exit_code == 1


# ── route ───────────────────────────────────────────────────────────────────────


def test_route_preview(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(
        app, ["org", "route", "optimize the slow latency", "--json"]
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    # the path now starts at the permanent administrator (Lhassa) at the root.
    assert payload["path"] == ["lhassa", "general", "dept:optimization"]
    assert payload["leaf"] == "dept:optimization"


# ── run (offline) ───────────────────────────────────────────────────────────────


def test_run_offline(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    monkeypatch.delenv("LHASSA_ORG_LIVE", raising=False)
    res = runner.invoke(
        app, ["org", "run", "reconcile the budget invoice", "--json"]
    )
    assert res.exit_code == 0
    out = json.loads(res.output)
    assert out["leaf"] == "dept:finance"
    assert out["department"] == "finance"
    # offline executor → deterministic, $0 result (no live model)
    assert out["result"]["model"] == "offline"
    assert out["result"]["cost_usd"] == 0.0


# ── skills / limits (define what a brain handles + its limitations) ───────────────


def test_add_with_skills_and_limits(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(
        app,
        [
            "org", "add", "--id", "dept:doc", "--parent", "general",
            "--department", "documentation",
            "--skills", "optimization,writing",
            "--limits", '{"budget_usd": 1.5}',
            "--json",
        ],
    )
    assert res.exit_code == 0
    node = json.loads(res.output)
    assert node["skills"] == ["optimization", "writing"]
    assert node["limits"] == {"budget_usd": 1.5}
    persisted = {n["id"]: n for n in json.loads(topo_file.read_text())}
    assert persisted["dept:doc"]["skills"] == ["optimization", "writing"]


def test_set_skills_cmd(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(
        app,
        ["org", "set-skills", "dept:finance", "--skills", "finance,budgets", "--json"],
    )
    assert res.exit_code == 0
    assert json.loads(res.output)["skills"] == ["finance", "budgets"]


def test_set_limits_cmd_kv_pairs(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(
        app,
        ["org", "set-limits", "dept:finance", "--limits", "max_risk=low,budget_usd=2.5",
         "--json"],
    )
    assert res.exit_code == 0
    limits = json.loads(res.output)["limits"]
    assert limits == {"max_risk": "low", "budget_usd": 2.5}


# ── move (re-parent) / promote (make root) ─────────────────────────────────────────


def test_move_reparents(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(
        app, ["org", "move", "dept:finance", "--parent", "dept:optimization"]
    )
    assert res.exit_code == 0
    persisted = {n["id"]: n for n in json.loads(topo_file.read_text())}
    assert persisted["dept:finance"]["parent"] == "dept:optimization"


def test_promote_makes_top_level_under_admin(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    topo_file.write_text(
        json.dumps([
            {"id": "general", "role": "general", "parent": None},
            {"id": "ops", "role": "sub_general", "parent": "general"},
            {"id": "dept:opt", "role": "department", "department": "optimization",
             "parent": "ops"},
        ])
    )
    res = runner.invoke(app, ["org", "promote", "dept:opt", "--json"])
    assert res.exit_code == 0
    by_id = {n["id"]: n for n in json.loads(res.output)}
    # Lhassa stays the permanent root; dept:opt becomes a direct child of it.
    assert by_id["lhassa"]["parent"] is None
    assert by_id["dept:opt"]["parent"] == "lhassa"
    assert by_id["general"]["parent"] == "lhassa"
    assert by_id["ops"]["parent"] == "general"


def test_remove_admin_rejected(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(app, ["org", "remove", "--id", "lhassa"])
    assert res.exit_code == 1


def test_tree_marks_admin(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(app, ["org", "tree"])
    assert res.exit_code == 0
    assert "★" in res.output and "admin" in res.output


# ── show / skills ──────────────────────────────────────────────────────────────────


def test_show_one_brain(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(app, ["org", "show", "dept:optimization", "--json"])
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["id"] == "dept:optimization"
    assert payload["is_leaf"] is True
    assert payload["children"] == []


def test_skills_lists_registry_domains(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(app, ["org", "skills", "--json"])
    assert res.exit_code == 0
    domains = {s["domain"] for s in json.loads(res.output)}
    assert "optimization" in domains


def test_tree_shows_skills(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    runner.invoke(app, ["org", "set-skills", "dept:optimization", "--skills", "optimization"])
    res = runner.invoke(app, ["org", "tree"])
    assert res.exit_code == 0
    assert "skills=optimization" in res.output


# ── the default chat/REPL path is preserved ──────────────────────────────────────

_FAKE = CliResult(
    response="# Hi\n\nHello there.",
    model="claude-haiku-4-5",
    input_tokens=12,
    output_tokens=5,
    cost_usd=0.0001,
)


def test_org_is_registered_in_help():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "org" in res.output


def test_default_prompt_still_routes_to_chat():
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE) as m:
        res = runner.invoke(app, ["hello there"])
    assert res.exit_code == 0
    assert "Hello there." in res.output
    m.assert_called_once()
