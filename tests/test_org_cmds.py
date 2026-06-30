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
                    "id": "dept:optimizacion",
                    "role": "department",
                    "department": "optimizacion",
                    "parent": "general",
                },
                {
                    "id": "dept:finanzas",
                    "role": "department",
                    "department": "finanzas",
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
    assert {r["id"] for r in rows} == {"general", "dept:optimizacion", "dept:finanzas"}


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
    assert any(r["id"] == "dept:finanzas" and r["department"] == "finanzas" for r in rows)


def test_default_derivation_when_no_file(tmp_path, monkeypatch):
    # No topology file written → derived standalone single brain.
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(app, ["org", "list", "--json"])
    assert res.exit_code == 0
    rows = json.loads(res.output)
    assert len(rows) == 1
    assert rows[0]["id"] == "general"


# ── add ─────────────────────────────────────────────────────────────────────────


def test_add_persists(tmp_path, monkeypatch):
    topo_file = _use_tmp(tmp_path, monkeypatch)
    _seed_flat(topo_file)
    res = runner.invoke(
        app,
        [
            "org", "add",
            "--id", "dept:documentacion",
            "--role", "department",
            "--parent", "general",
            "--department", "documentacion",
            "--json",
        ],
    )
    assert res.exit_code == 0
    assert json.loads(res.output)["id"] == "dept:documentacion"
    # persisted to disk
    ids = {n["id"] for n in json.loads(topo_file.read_text())}
    assert "dept:documentacion" in ids


def test_add_into_empty_creates_hierarchy(tmp_path, monkeypatch):
    # Fresh (no file) → standalone general; adding a child builds a 2-level chart.
    topo_file = _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(
        app,
        ["org", "add", "--id", "dept:x", "--parent", "general", "--department", "x"],
    )
    assert res.exit_code == 0
    ids = {n["id"] for n in json.loads(topo_file.read_text())}
    assert ids == {"general", "dept:x"}


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
    res = runner.invoke(app, ["org", "remove", "--id", "dept:finanzas", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["removed"] == ["dept:finanzas"]
    ids = {n["id"] for n in json.loads(topo_file.read_text())}
    assert "dept:finanzas" not in ids


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
                    "id": "dept:optimizacion",
                    "role": "department",
                    "department": "optimizacion",
                    "parent": "ops",
                },
            ]
        )
    )
    res = runner.invoke(app, ["org", "remove", "--id", "ops", "--force", "--json"])
    assert res.exit_code == 0
    removed = set(json.loads(res.output)["removed"])
    assert removed == {"ops", "dept:optimizacion"}
    ids = {n["id"] for n in json.loads(topo_file.read_text())}
    assert ids == {"general"}


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
    assert payload["path"] == ["general", "dept:optimizacion"]
    assert payload["leaf"] == "dept:optimizacion"


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
    assert out["leaf"] == "dept:finanzas"
    assert out["department"] == "finanzas"
    # offline executor → deterministic, $0 result (no live model)
    assert out["result"]["model"] == "offline"
    assert out["result"]["cost_usd"] == 0.0


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
