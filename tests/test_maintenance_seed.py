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
    # — we only care that it ran and the temple chain logged, not its content.
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
