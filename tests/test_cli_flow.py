"""``lhassa flow``: the singular work-flow lifecycle (create/edit/validate/run/…).

Every test is offline: a tmp flows file and a tmp brain DB. The load-bearing
invariant asserted here is that a CLI run goes through
``lhassa.org.delegate.delegate_flow`` and NEVER through
``lhassa.flows.engine.run_flow`` directly; a flow is never run bare.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from typer.testing import CliRunner


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """A CliRunner with the flows file + brain DB pointed at ``tmp_path``."""
    import lhassa.config as config_mod
    import lhassa.core.db as db
    from lhassa.security import signing

    monkeypatch.setattr(config_mod.Config, "flows_file", tmp_path / "flows.json")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    asyncio.run(db.init_db())

    from lhassa.cli.app import app

    runner = CliRunner()
    return lambda *args: runner.invoke(app, list(args))


def _flows(tmp_path) -> dict:
    path = tmp_path / "flows.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ── create ───────────────────────────────────────────────────────────────────────


def test_create_blank_makes_one_trigger_flow(cli, tmp_path):
    result = cli("flow", "create", "My Test Flow")
    assert result.exit_code == 0, result.output
    stored = _flows(tmp_path)
    assert "my-test-flow" in stored
    nodes = stored["my-test-flow"]["nodes"]
    assert [n["kind"] for n in nodes] == ["trigger"]


def test_create_from_template_keeps_the_template_shape(cli, tmp_path):
    result = cli("flow", "create", "Brief", "--template", "research-brief", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["id"] == "brief"
    assert payload["name"] == "Brief"
    assert len(payload["nodes"]) > 1


def test_create_rejects_template_and_blank_together(cli):
    result = cli("flow", "create", "X", "--template", "research-brief", "--blank")
    assert result.exit_code == 2


def test_create_deduplicates_the_generated_id(cli, tmp_path):
    assert cli("flow", "create", "Dup").exit_code == 0
    assert cli("flow", "create", "Dup").exit_code == 0
    assert set(_flows(tmp_path)) == {"dup", "dup-2"}


# ── validate ─────────────────────────────────────────────────────────────────────


def test_validate_passes_for_a_stored_flow(cli):
    cli("flow", "create", "Ok Flow")
    result = cli("flow", "validate", "ok-flow")
    assert result.exit_code == 0
    assert "valid" in result.output


def test_validate_fails_for_an_unknown_target(cli):
    assert cli("flow", "validate", "nope").exit_code == 1


def test_validate_reads_a_file_path_without_persisting(cli, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"id": "b", "name": "B", "nodes": [], "edges": []}))
    result = cli("flow", "validate", str(bad))
    # No trigger node → invalid, and nothing was written to the store.
    assert result.exit_code == 1
    assert "b" not in _flows(tmp_path)


# ── edit ─────────────────────────────────────────────────────────────────────────


def test_edit_persists_a_valid_buffer(cli, monkeypatch, tmp_path):
    cli("flow", "create", "Editable")

    def fake_call(argv):
        path = argv[-1]
        data = json.loads(open(path, encoding="utf-8").read())
        data["description"] = "edited by the test"
        open(path, "w", encoding="utf-8").write(json.dumps(data))
        return 0

    monkeypatch.setattr("lhassa.cli.flow_cmds.subprocess.call", fake_call)
    result = cli("flow", "edit", "editable")
    assert result.exit_code == 0, result.output
    assert _flows(tmp_path)["editable"]["description"] == "edited by the test"


def test_edit_never_persists_an_invalid_buffer(cli, monkeypatch, tmp_path):
    cli("flow", "create", "Fragile")
    before = _flows(tmp_path)["fragile"]

    def fake_call(argv):
        open(argv[-1], "w", encoding="utf-8").write("{ not json")
        return 0

    monkeypatch.setattr("lhassa.cli.flow_cmds.subprocess.call", fake_call)
    # Decline the re-open offer → abort, store untouched.
    monkeypatch.setattr("lhassa.cli.flow_cmds.typer.confirm", lambda *a, **k: False)
    result = cli("flow", "edit", "fragile")
    assert result.exit_code == 1
    assert _flows(tmp_path)["fragile"] == before
    assert "preserved at" in result.output


def test_edit_from_json_persists_without_an_editor(cli, monkeypatch, tmp_path):
    """The non-interactive path is what lets Lhassa author its own flows."""
    cli("flow", "create", "Headless")

    def explode(argv):  # an editor must never be launched on this path
        raise AssertionError("--from-json must not open an editor")

    monkeypatch.setattr("lhassa.cli.flow_cmds.subprocess.call", explode)
    doc = _flows(tmp_path)["headless"]
    doc["description"] = "authored headlessly"
    src = tmp_path / "flow.json"
    src.write_text(json.dumps(doc), encoding="utf-8")

    result = cli("flow", "edit", "headless", "--from-json", str(src))
    assert result.exit_code == 0, result.output
    assert _flows(tmp_path)["headless"]["description"] == "authored headlessly"


def test_edit_from_json_rejects_an_invalid_document(cli, monkeypatch, tmp_path):
    cli("flow", "create", "Guarded")
    before = _flows(tmp_path)["guarded"]
    src = tmp_path / "bad.json"
    src.write_text("{ not json", encoding="utf-8")

    result = cli("flow", "edit", "guarded", "--from-json", str(src))
    assert result.exit_code == 1
    assert _flows(tmp_path)["guarded"] == before  # store untouched


# ── run (the delegate_flow invariant) ────────────────────────────────────────────


def test_run_goes_through_delegate_flow_not_run_flow(cli, monkeypatch):
    cli("flow", "create", "Runnable")
    seen = {}

    async def fake_delegate(flow, **kwargs):
        seen["flow_id"] = flow.id
        seen["inputs"] = kwargs.get("inputs")
        seen["durable"] = kwargs.get("durable")
        return {"id": "run_fake", "flow_id": flow.id, "status": "completed", "steps": []}

    async def explode(*args, **kwargs):  # run_flow must never be reached from the CLI
        raise AssertionError("the CLI must not call run_flow directly")

    monkeypatch.setattr("lhassa.org.delegate.delegate_flow", fake_delegate)
    monkeypatch.setattr("lhassa.flows.engine.run_flow", explode)

    result = cli("flow", "run", "runnable", "--input", '{"topic": "x"}')
    assert result.exit_code == 0, result.output
    assert seen == {"flow_id": "runnable", "inputs": {"topic": "x"}, "durable": True}
    assert "run_fake" in result.output


def test_run_wraps_bare_input_text_as_summary(cli, monkeypatch):
    cli("flow", "create", "Bare")
    seen = {}

    async def fake_delegate(flow, **kwargs):
        seen.update(kwargs.get("inputs") or {})
        return {"id": "r1", "flow_id": flow.id, "status": "completed", "steps": []}

    monkeypatch.setattr("lhassa.org.delegate.delegate_flow", fake_delegate)
    assert cli("flow", "run", "bare", "--input", "just text").exit_code == 0
    assert seen == {"summary": "just text"}


def test_run_of_an_unknown_flow_exits_nonzero(cli):
    assert cli("flow", "run", "ghost").exit_code == 1


# ── runs / logs / watch (durable kernel reads) ───────────────────────────────────


def test_runs_lists_nothing_on_a_fresh_brain(cli):
    result = cli("flow", "runs", "--json")
    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_logs_of_an_unknown_run_exits_nonzero(cli):
    assert cli("flow", "logs", "run_missing").exit_code == 1


def test_watch_of_an_unknown_run_exits_nonzero(cli):
    assert cli("flow", "watch", "run_missing").exit_code == 1


# ── delete ───────────────────────────────────────────────────────────────────────


def test_delete_with_yes_removes_the_flow(cli, tmp_path):
    cli("flow", "create", "Doomed")
    assert cli("flow", "delete", "doomed", "--yes").exit_code == 0
    assert "doomed" not in _flows(tmp_path)


def test_delete_without_confirmation_keeps_the_flow(cli, monkeypatch, tmp_path):
    cli("flow", "create", "Safe")
    monkeypatch.setattr("lhassa.cli.flow_cmds.typer.confirm", lambda *a, **k: False)
    assert cli("flow", "delete", "safe").exit_code == 1
    assert "safe" in _flows(tmp_path)


# ── the plural sub-app still works unchanged ─────────────────────────────────────


def test_plural_flows_list_still_works(cli):
    cli("flow", "create", "Legacy")
    result = cli("flows", "list", "--json")
    assert result.exit_code == 0
    assert [f["id"] for f in json.loads(result.output)] == ["legacy"]


def test_plural_flows_templates_still_works(cli):
    result = cli("flows", "templates", "--json")
    assert result.exit_code == 0
    assert any(t["id"] == "research-brief" for t in json.loads(result.output))
