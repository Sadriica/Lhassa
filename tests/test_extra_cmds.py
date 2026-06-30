"""Tests for the Lhassa CLI action commands (status/version/serve/beat/watch).

Offline: ``status`` is driven against a canned overview (the aggregate itself is
covered by test_overview_api); ``beat``/``watch`` run against a tmp brain DB;
``serve`` is smoke-tested for arg parsing with its bind patched away (it never binds
a port).
"""

import asyncio
import json
from unittest.mock import patch

from typer.testing import CliRunner

from lhassa.cli.app import app
from lhassa.config import Config

runner = CliRunner()

INST = Config.instance_id

_FAKE_OVERVIEW = {
    "brains": {"count": 2, "admin": "lhassa", "root": "lhassa", "standalone": False},
    "skills": {"installed": 3, "recommended": 5},
    "approvals": {
        "pending": 1,
        "items": [
            {
                "id": "ap_1",
                "tipo": "send_email",
                "accion": "send_email",
                "actor_id": "duenio",
                "summary": "send_email",
                "creado_en": "2026-06-30T10:00:00",
            }
        ],
    },
    "cost": {
        "today_cost_usd": 0.1234,
        "daily_token_budget": 100000,
        "output_tokens_today": 42,
        "kill_switch": False,
    },
    "health": {"worst_level": "info", "ok": True},
    "activity": {
        "recent": [
            {
                "id": "ep_1",
                "creado_en": "2026-06-30T10:00:00",
                "despertar_por": "heartbeat",
                "decision": "rest",
                "resultado": "all quiet",
            }
        ]
    },
    "tools": {"count": 1, "by_estado": {"cuarentena": 1}},
    "errors": {},
    "instance_id": INST,
    "generated_at": 0,
}


def _use_tmp_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    return db


# ── status / overview ─────────────────────────────────────────────────────────────

def test_status_json():
    with patch("lhassa.cli.extra_cmds.load_overview", return_value=_FAKE_OVERVIEW):
        res = runner.invoke(app, ["status", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["brains"]["count"] == 2
    assert data["approvals"]["pending"] == 1


def test_status_pretty():
    with patch("lhassa.cli.extra_cmds.load_overview", return_value=_FAKE_OVERVIEW):
        res = runner.invoke(app, ["status"])
    assert res.exit_code == 0
    assert "overview" in res.output
    assert "Brains" in res.output
    assert "send_email" in res.output


def test_overview_alias():
    with patch("lhassa.cli.extra_cmds.load_overview", return_value=_FAKE_OVERVIEW):
        res = runner.invoke(app, ["overview", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["tools"]["count"] == 1


# ── version ───────────────────────────────────────────────────────────────────────

def test_version_json():
    res = runner.invoke(app, ["version", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert "version" in data
    assert "python" in data
    assert "toggles" in data


def test_version_pretty():
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert "Lhassa" in res.output


# ── serve (arg parsing only — never binds a port) ─────────────────────────────────

def test_serve_parses_args_without_binding():
    with patch("lhassa.cli.extra_cmds._serve") as m:
        res = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "9123"])
    assert res.exit_code == 0
    m.assert_called_once_with("0.0.0.0", 9123)
    # The URL hint uses 'localhost' for a wildcard bind.
    assert "9123" in res.output


def test_serve_default_args():
    with patch("lhassa.cli.extra_cmds._serve") as m:
        res = runner.invoke(app, ["serve"])
    assert res.exit_code == 0
    m.assert_called_once_with("127.0.0.1", 7766)


# ── beat (one heartbeat tick against a tmp DB) ────────────────────────────────────

def test_beat_json(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    asyncio.run(db.init_db())

    res = runner.invoke(app, ["beat", "--json"])
    assert res.exit_code == 0
    summary = json.loads(res.output)
    assert "triggers" in summary


def test_beat_pretty(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    asyncio.run(db.init_db())

    res = runner.invoke(app, ["beat"])
    assert res.exit_code == 0
    assert "heartbeat" in res.output


# ── watch (single frame, no loop) ─────────────────────────────────────────────────

def test_watch_once(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    from lhassa.core.episode import Episode

    async def seed():
        await db.init_db()
        ep = Episode.begin(despertar_por="heartbeat", instance_id=INST)
        ep.finish(decision="rest", accion="noop", resultado="all quiet")
        await ep.append()

    asyncio.run(seed())

    res = runner.invoke(app, ["watch", "--once"])
    assert res.exit_code == 0
    assert "activity" in res.output
    assert "heartbeat" in res.output


# ── the new commands are listed + default chat still works ────────────────────────

def test_help_lists_new_commands():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    for name in ("status", "version", "serve", "beat", "watch"):
        assert name in res.output


def test_status_is_not_routed_to_chat():
    # 'status' must hit the status command, never the chat path.
    with patch("lhassa.cli.app._run_sync") as m, patch(
        "lhassa.cli.extra_cmds.load_overview", return_value=_FAKE_OVERVIEW
    ):
        res = runner.invoke(app, ["status", "--json"])
    assert res.exit_code == 0
    m.assert_not_called()
