# SPDX-License-Identifier: AGPL-3.0-or-later
"""Queue hygiene: unanswered self-proposals close by timeout or by cap, never silently
on anything something waits for, and never as a human decision."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from lhassa.security import approval

INST = "inst_exp"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.security import signing

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    monkeypatch.setattr(signing, "KEY_PATH", tmp_path / "keys" / "ed25519")
    asyncio.run(db.init_db())
    return db


def _add(db, tipo, age_days, payload=None, estado="pendiente"):
    async def go():
        ap_id = await db.add_approval(INST, tipo, payload or {"accion": tipo}, estado=estado)
        created = (NOW - timedelta(days=age_days)).isoformat()
        import sqlite3

        con = sqlite3.connect(db.DB_PATH)
        con.execute("UPDATE approvals SET creado_en = ? WHERE id = ?", (created, ap_id))
        con.commit()
        con.close()
        return ap_id

    return asyncio.run(go())


def _state(db, ap_id):
    return asyncio.run(db.get_approval(INST, ap_id))


def _expire(**kw):
    return asyncio.run(approval.expire_stale(INST, now=NOW, **kw))


def test_old_self_proposals_close_by_timeout_and_say_why(brain):
    old = _add(brain, "retire_capability", 45)
    fresh = _add(brain, "retire_capability", 10)
    out = _expire(days=30, cap=0)
    assert out["timeout"] == [old] and out["cap"] == [] and out["kept"] == 1
    row = _state(brain, old)
    assert row["estado"] == "rechazada" and row["resuelto_en"]
    assert row["payload"]["resolution"] == "rejected-by-timeout"
    assert row["payload"]["accion"] == "retire_capability"  # the original payload survives
    assert _state(brain, fresh)["estado"] == "pendiente"


def test_the_cap_closes_the_oldest_beyond_it_per_type(brain):
    ids = [_add(brain, "mint_script", 20 - i) for i in range(5)]  # oldest first
    other = _add(brain, "mint_memory", 1)
    out = _expire(days=0, cap=3)
    assert out["cap"] == ids[:2] and out["timeout"] == []
    assert [_state(brain, i)["estado"] for i in ids] == ["rechazada", "rechazada", "pendiente", "pendiente", "pendiente"]
    assert _state(brain, ids[0])["payload"]["resolution"] == "rejected-by-cap"
    assert _state(brain, other)["estado"] == "pendiente"


def test_only_task_free_self_proposals_are_ever_expired(brain):
    parked = _add(brain, "mint_script", 90, {"accion": "mint_script", "task_id": "t1"})
    gate = _add(brain, "flow_gate", 90, {"run_id": "r1"})
    task = _add(brain, "task", 90, {"accion": "run_command"})
    out = _expire(days=30, cap=1)
    assert out["timeout"] == [] and out["cap"] == []
    for ap in (parked, gate, task):
        assert _state(brain, ap)["estado"] == "pendiente"


def test_dry_run_writes_nothing(brain):
    old = _add(brain, "retire_capability", 99)
    out = _expire(days=30, cap=0, dry_run=True)
    assert out["timeout"] == [old]
    assert _state(brain, old)["estado"] == "pendiente"


def test_zero_disables_both_rules(brain):
    _add(brain, "retire_capability", 400)
    assert _expire(days=0, cap=0) == {"timeout": [], "cap": [], "kept": 1}


def test_a_closure_is_not_a_human_decision(brain, monkeypatch):
    called = []

    async def spy(*a, **kw):
        called.append(a)

    monkeypatch.setattr(approval.autonomy, "record_decision", spy)
    _add(brain, "retire_capability", 99)
    _expire(days=30, cap=0)
    assert called == []  # no streak reset, no feedback


def test_closing_twice_is_a_noop(brain):
    old = _add(brain, "retire_capability", 99)
    assert asyncio.run(brain.close_approval(INST, old, "rejected-by-timeout")) is True
    assert asyncio.run(brain.close_approval(INST, old, "rejected-by-timeout")) is False


def test_resolved_rows_are_left_alone(brain):
    done = _add(brain, "retire_capability", 99, estado="aprobada")
    _expire(days=30, cap=0)
    assert _state(brain, done)["estado"] == "aprobada"


def test_pending_count(brain):
    _add(brain, "mint_script", 1)
    _add(brain, "mint_script", 2)
    _add(brain, "mint_memory", 2)
    assert asyncio.run(approval.pending_count(INST, "mint_script")) == 2


# ── the CLI ──────────────────────────────────────────────────────────────────

def test_cli_expire_and_the_bare_list_still_works(brain, monkeypatch):
    from lhassa.cli.app import app
    from lhassa.config import Config

    monkeypatch.setattr(Config, "instance_id", INST)
    old = _add(brain, "retire_capability", 99)
    _add(brain, "retire_capability", 1)
    runner = CliRunner()

    listed = runner.invoke(app, ["approvals", "--json"])
    assert listed.exit_code == 0 and len(json.loads(listed.output)) == 2

    dry = runner.invoke(app, ["approvals", "expire", "--dry-run", "--days", "30", "--cap", "0"])
    assert dry.exit_code == 0 and "would close 1 by timeout" in dry.output
    assert _state(brain, old)["estado"] == "pendiente"

    res = runner.invoke(app, ["approvals", "expire", "--days", "30", "--cap", "0", "--json"])
    assert res.exit_code == 0 and json.loads(res.output)["timeout"] == [old]
    assert _state(brain, old)["estado"] == "rechazada"


# ── the daily hook in the heartbeat ──────────────────────────────────────────

def test_step_expires_once_per_day(brain, monkeypatch):
    from lhassa.core import step as step_mod

    _add(brain, "retire_capability", 999)  # relative to the fixed NOW; far past the limit
    calls = []
    real = approval.expire_stale

    async def spy(*a, **kw):
        calls.append(1)
        return await real(*a, **kw)

    monkeypatch.setattr(approval, "expire_stale", spy)
    first = asyncio.run(step_mod._expire_approvals_daily(INST))
    second = asyncio.run(step_mod._expire_approvals_daily(INST))
    assert first == 1 and second == 0 and len(calls) == 1


def test_a_failing_expiry_never_breaks_the_tick(brain, monkeypatch):
    from lhassa.core import step as step_mod

    async def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(approval, "expire_stale", boom)
    assert asyncio.run(step_mod._expire_approvals_daily(INST)) == 0
