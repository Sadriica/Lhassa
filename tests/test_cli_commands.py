"""Tests for the Lhassa CLI power subcommands + preserved default chat/REPL path.

Fully offline: the brain DB is redirected to a tmp file, the gateway ledger to a
tmp file, ccusage is stubbed, and the chat path's ``_run_sync`` is mocked so no
real LLM call is made.
"""

import asyncio
import json
from unittest.mock import patch

from typer.testing import CliRunner

from lhassa.cli.agent import CliResult
from lhassa.cli.app import app
from lhassa.config import Config

runner = CliRunner()

INST = Config.instance_id

_FAKE = CliResult(
    response="# Hi\n\nHello there.",
    model="claude-haiku-4-5",
    input_tokens=12,
    output_tokens=5,
    cost_usd=0.0001,
)


def _use_tmp_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    return db


# ── default chat / REPL path still works ─────────────────────────────────────────

def test_default_prompt_still_works():
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE):
        result = runner.invoke(app, ["ping"])
    assert result.exit_code == 0
    assert "Hello there." in result.output


def test_default_prompt_json_still_works():
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE):
        result = runner.invoke(app, ["ping", "--json"])
    assert result.exit_code == 0
    obj = json.loads(result.output.strip())
    assert obj["model"] == _FAKE.model


def test_piped_stdin_still_works():
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE) as m:
        result = runner.invoke(app, [], input="summarize this piped text")
    assert result.exit_code == 0
    assert "summarize this piped text" in m.call_args[0][0]


def test_no_prompt_no_stdin_exits_nonzero():
    with patch("lhassa.cli.app._run_sync", return_value=_FAKE):
        result = runner.invoke(app, [], input="")
    assert result.exit_code == 1


# ── tools ────────────────────────────────────────────────────────────────────────

def test_tools_cmd(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)

    async def seed():
        await db.init_db()
        await db.upsert_tool(
            INST, "speedup", dominio="optimization", estado="cuarentena",
            metrica_actual=6.0,
        )

    asyncio.run(seed())

    res = runner.invoke(app, ["tools", "--json"])
    assert res.exit_code == 0
    rows = json.loads(res.output)
    row = next(r for r in rows if r["nombre"] == "speedup")
    assert row["estado"] == "cuarentena"
    assert row["dominio"] == "optimization"

    # Pretty path runs without error.
    assert runner.invoke(app, ["tools"]).exit_code == 0


# ── approvals / approve / deny ───────────────────────────────────────────────────

def test_approvals_and_approve(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)

    async def seed():
        await db.init_db()
        return await db.add_approval(
            INST, "send_email",
            {"accion": "send_email", "actor_id": "duenio", "origen_trust": 2},
        )

    approval_id = asyncio.run(seed())

    res = runner.invoke(app, ["approvals", "--json"])
    assert res.exit_code == 0
    rows = json.loads(res.output)
    assert any(r["id"] == approval_id for r in rows)

    res = runner.invoke(app, ["approve", approval_id, "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["approved"] is True

    # Now the queue is empty.
    remaining = asyncio.run(db.load_pending_approvals(INST))
    assert remaining == []


def test_deny(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)

    async def seed():
        await db.init_db()
        return await db.add_approval(
            INST, "delete_data",
            {"accion": "delete_data", "actor_id": "duenio", "origen_trust": 2},
        )

    approval_id = asyncio.run(seed())
    res = runner.invoke(app, ["deny", approval_id, "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["approved"] is False
    assert asyncio.run(db.load_pending_approvals(INST)) == []


# ── report ───────────────────────────────────────────────────────────────────────

def test_report_json(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)

    from lhassa.llm import gateway as gw_mod

    monkeypatch.setattr(gw_mod.gateway, "db_path", tmp_path / "gw.db")
    monkeypatch.setattr(gw_mod.gateway, "_initialized", False)

    async def fake_ccusage(*a, **k):
        return {"available": False, "by_harness": {}, "total_cost_usd": 0.0}

    import lhassa.llm.ccusage as ccusage_mod
    monkeypatch.setattr(ccusage_mod, "read_ccusage", fake_ccusage)

    res = runner.invoke(app, ["report", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["window_days"] == 7
    assert "total_cost_usd" in data
    assert "by_model" in data
    assert data["ccusage"]["available"] is False


# ── memory ───────────────────────────────────────────────────────────────────────

def test_memory_search(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    from lhassa.memory import facts

    async def seed():
        await db.init_db()
        await facts.add_fact(INST, "dato", "le gusta el chocolate con menta", tags=["gustos"])
        await facts.add_fact(INST, "dato", "vive en Madrid", tags=["lugar"])

    asyncio.run(seed())

    res = runner.invoke(app, ["memory", "chocolate", "--json"])
    assert res.exit_code == 0
    rows = json.loads(res.output)
    assert len(rows) == 1
    assert "chocolate" in rows[0]["contenido"]

    # Tag match.
    res = runner.invoke(app, ["memory", "lugar", "--json"])
    assert json.loads(res.output)[0]["contenido"] == "vive en Madrid"


# ── fleet ────────────────────────────────────────────────────────────────────────

def test_fleet(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    from lhassa.core.episode import Episode

    async def seed():
        await db.init_db()
        await db.upsert_desired_state(INST, "laptop", "repoA", "main")
        ep = Episode.begin(despertar_por="checkin:laptop", instance_id=INST)
        ep.finish(decision="checkin", accion="checkin", resultado="ok")
        await ep.append()

    asyncio.run(seed())

    res = runner.invoke(app, ["fleet", "--json"])
    assert res.exit_code == 0
    rows = json.loads(res.output)
    laptop = next(r for r in rows if r["machine_id"] == "laptop")
    assert laptop["desired_state"] == {"repoA": "main"}
    assert laptop["last_checkin"]


# ── routing: a subcommand name is NOT treated as a chat prompt ───────────────────

def test_subcommand_not_routed_to_chat(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    asyncio.run(db.init_db())
    # 'tools' must hit the tools command, never the chat path.
    with patch("lhassa.cli.app._run_sync") as m:
        res = runner.invoke(app, ["tools", "--json"])
    assert res.exit_code == 0
    m.assert_not_called()
