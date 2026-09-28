"""Tests for the ``maintenance`` seed's routing through temple (temple_run).

Offline + $0: monkeypatches ``lhassa.core.db.DB_PATH`` to a tmp file so the
seed's minted tasks land in an isolated DB, exactly like tests/test_cv_seed.py.
Covers:

  * every recurring action is minted as ``temple_run`` (not bare), with the
    real action name carried in ``params["action"]``;
  * re-applying the seed is idempotent (no duplicate temple_run tasks);
  * a temple_run task, when actually run, executes the wrapped action AND
    logs the temple down-execute-up episode chain (dominio="temple").
"""

import asyncio

import pytest

from lhassa.core import db
from lhassa.seeds import maintenance as maint_seed


class _Cfg:
    def __init__(self, instance_id="inst_personal"):
        self.instance_id = instance_id


@pytest.fixture
def _db(tmp_path, monkeypatch):
    from lhassa import actionpack

    actionpack.register_all()  # temple_run must be registered for mint_task to accept it
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    return db


def test_maintenance_actions_are_wrapped_in_temple_run(_db):
    summary = maint_seed.seed_maintenance(_Cfg())
    assert summary["errors"] == []
    assert set(summary["created"]) == {a for a, _ in maint_seed._RECURRING_ACTIONS}

    tasks = asyncio.run(_db.load_pending_tasks("inst_personal"))
    accions = {t["accion"] for t in tasks}
    assert accions == {"temple_run"}

    wrapped = {t["params"]["action"] for t in tasks}
    assert wrapped == {a for a, _ in maint_seed._RECURRING_ACTIONS}


def test_maintenance_seed_is_idempotent(_db):
    maint_seed.seed_maintenance(_Cfg())
    before = asyncio.run(_db.load_pending_tasks("inst_personal"))

    second = maint_seed.seed_maintenance(_Cfg())
    assert second["created"] == []
    assert set(second["existing"]) == {a for a, _ in maint_seed._RECURRING_ACTIONS}

    after = asyncio.run(_db.load_pending_tasks("inst_personal"))
    assert len(after) == len(before)


def test_idempotent_even_when_existing_task_is_future_due(_db):
    """The subtle pile-up bug: once step.py re-mints a recurring wrapper with a
    FUTURE due_at, a due-aware lookup (load_pending_tasks) can't see it, so the
    seed re-mints a duplicate every apply. The check must use ALL pending tasks."""
    maint_seed.seed_maintenance(_Cfg())

    # Simulate the post-fire state: push every wrapper's due_at into the future.
    import sqlite3

    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "UPDATE tasks SET due_at = '2099-01-01T00:00:00+00:00' "
        "WHERE accion = 'temple_run' AND estado = 'pendiente'"
    )
    conn.commit()
    conn.close()

    # None of these are "due" now, but the seed must still recognise them.
    second = maint_seed.seed_maintenance(_Cfg())
    assert second["created"] == []
    assert set(second["existing"]) == {a for a, _ in maint_seed._RECURRING_ACTIONS}

    all_pending = asyncio.run(db.list_tasks("inst_personal", estado="pendiente", limit=500))
    temple = [t for t in all_pending if t["accion"] == "temple_run"]
    assert len(temple) == len(maint_seed._RECURRING_ACTIONS)  # no pile-up


@pytest.mark.asyncio
async def test_a_wrapped_maintenance_task_runs_through_temple(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from types import SimpleNamespace

    from lhassa.org import topology as topo_mod
    from lhassa.org.topology import BrainNode, Topology

    def _topology():
        return Topology(
            [
                BrainNode(id="lhassa", role="administrator"),
                BrainNode(id="temple", role="internal-operations", department="temple", parent="lhassa"),
            ]
        )

    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology())

    from lhassa.notify import dispatch as notify_dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)

    # distill_memory with no episodes to distill degrades to a cheap no-op string
    #; we only care that it ran and the temple chain logged, not its content.
    from lhassa.actionpack._temple import temple_run

    cfg = SimpleNamespace(
        instance_id="inst_personal", root_admin_id="lhassa", notify_channels="log"
    )
    result = await temple_run({"action": "distill_memory", "params": {}, "config": cfg})
    assert "temple_run: distill_memory" in result

    rows = await db.load_recent_episodes("inst_personal", n=50)
    domains = {r["despertar_por"] for r in rows}
    assert "org-delegate" in domains
    assert "temple-execute" in domains
    assert any(r["dominio"] == "temple" for r in rows if r["despertar_por"] == "temple-execute")


def test_unwrapped_action_is_refused():
    import asyncio as _asyncio

    from lhassa.actionpack._temple import temple_run

    result = _asyncio.run(temple_run({"action": "run_command"}))
    assert "not routed through temple" in result


# ── retuning a cadence that changed in the declaration ───────────────────────


def test_a_changed_cadence_retunes_the_live_task(_db, monkeypatch):
    """The declaration table is the source of truth for HOW OFTEN.

    Before this, the seed could only mint or skip, so editing an interval
    changed nothing for an already-seeded brain: `ingest_knowledge` was moved
    from weekly to daily and the live task stayed weekly forever. The edit
    looked applied and was not.
    """
    import sqlite3

    monkeypatch.setattr(
        maint_seed, "_RECURRING_ACTIONS", (("distill_memory", 604800),)
    )
    maint_seed.seed_maintenance(_Cfg())

    con = sqlite3.connect(str(_db.DB_PATH))
    assert con.execute("SELECT recurrence FROM tasks").fetchone()[0] == "604800"

    monkeypatch.setattr(
        maint_seed, "_RECURRING_ACTIONS", (("distill_memory", 86400),)
    )
    summary = maint_seed.seed_maintenance(_Cfg())
    assert summary["retuned"] == ["distill_memory"]
    assert summary["created"] == [] and summary["errors"] == []

    con = sqlite3.connect(str(_db.DB_PATH))
    rows = con.execute("SELECT recurrence FROM tasks").fetchall()
    assert len(rows) == 1 and rows[0][0] == "86400"  # retuned in place, not doubled


def test_a_retune_to_a_shorter_interval_pulls_the_next_run_in(_db, monkeypatch):
    """Half an applied fix: the cadence changes but the next occurrence was
    scheduled under the OLD one, so a weekly task retuned to daily still sleeps
    out its original weekly slot and nothing appears to have happened."""
    import sqlite3
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(
        maint_seed, "_RECURRING_ACTIONS", (("distill_memory", 604800),)
    )
    maint_seed.seed_maintenance(_Cfg())

    far = (datetime.now(timezone.utc) + timedelta(days=6)).isoformat()
    con = sqlite3.connect(str(_db.DB_PATH))
    con.execute("UPDATE tasks SET due_at = ?", (far,))
    con.commit()

    monkeypatch.setattr(
        maint_seed, "_RECURRING_ACTIONS", (("distill_memory", 86400),)
    )
    maint_seed.seed_maintenance(_Cfg())

    con = sqlite3.connect(str(_db.DB_PATH))
    due = datetime.fromisoformat(con.execute("SELECT due_at FROM tasks").fetchone()[0])
    assert due <= datetime.now(timezone.utc) + timedelta(seconds=86400 + 60)


def test_a_retune_to_a_longer_interval_never_pulls_work_earlier(_db, monkeypatch):
    """Lengthening an interval must not move the next run forward: the owner
    was promised that occurrence at that time."""
    import sqlite3
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(maint_seed, "_RECURRING_ACTIONS", (("distill_memory", 86400),))
    maint_seed.seed_maintenance(_Cfg())

    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    con = sqlite3.connect(str(_db.DB_PATH))
    con.execute("UPDATE tasks SET due_at = ?", (soon,))
    con.commit()

    monkeypatch.setattr(maint_seed, "_RECURRING_ACTIONS", (("distill_memory", 604800),))
    maint_seed.seed_maintenance(_Cfg())

    con = sqlite3.connect(str(_db.DB_PATH))
    assert con.execute("SELECT due_at FROM tasks").fetchone()[0] == soon  # untouched


def test_an_unparseable_due_date_is_left_alone_not_guessed(_db):
    assert maint_seed._clamped_due("not-a-date", 86400) is None
    assert maint_seed._clamped_due(None, 86400) is None
