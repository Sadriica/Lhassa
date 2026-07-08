"""Tests for lhassa.org.temple — the templo down-execute-up internal loop.

Offline + $0: a tmp brain DB (monkeypatched ``db.DB_PATH``), a seeded topology
(lhassa → templo, mirroring tests/test_org_delegate.py's shape), a fake
deterministic action registered under core.actions, and a fake ``deliver`` so
nothing touches the network. LHASSA_ORG_LIVE is left off (the deterministic
action path never needs it). Covers:

  * the full loop — a DOWN org-delegate episode, an execution episode
    attributed dominio="templo", a deliver() call with origin_department=
    "templo", and {"ok": True, ...} returned;
  * a topology failure degrades to a direct run — the action still executes
    and the result still comes back "ok";
  * a failing action still returns ok (delegate_internal never raises) with
    the error text as the result;
  * an unknown action is dispatched, executes, and reports "unknown action".
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.core import actions as core_actions
from lhassa.core import db
from lhassa.org import temple
from lhassa.org.topology import BrainNode, Topology


def _topology() -> Topology:
    """lhassa (root admin) → templo (a leaf department, no workers)."""
    return Topology(
        [
            BrainNode(id="lhassa", role="administrator"),
            BrainNode(id="templo", role="internal-operations", department="templo", parent="lhassa"),
        ]
    )


def _topology_with_workers() -> Topology:
    """lhassa → templo → the five worker brains (the "monks")."""
    nodes = [
        BrainNode(id="lhassa", role="administrator"),
        BrainNode(id="templo", role="internal-operations", department="templo", parent="lhassa"),
    ]
    for worker_id in temple.TEMPLE_WORKERS:
        nodes.append(
            BrainNode(id=worker_id, role="monk", department="templo", parent="templo")
        )
    return Topology(nodes)


def _cfg(tmp_path):
    topo = tmp_path / "org.json"
    topo.write_text(
        '[{"id":"templo","role":"internal-operations","department":"templo","parent":null}]',
        encoding="utf-8",
    )
    return SimpleNamespace(
        instance_id="inst_personal",
        org_topology_file=str(topo),
        root_admin_id="lhassa",
        notify_channels="log",
    )


async def _episodes(instance_id: str, despertar_por: str) -> list[dict]:
    rows = await db.load_recent_episodes(instance_id, n=100)
    rows.reverse()
    return [r for r in rows if r["despertar_por"] == despertar_por]


@pytest.fixture(autouse=True)
def _fake_action():
    """Register a tiny deterministic fake action for the duration of each test."""
    if core_actions._REGISTRY.get_or_none("temple_test_action") is None:
        core_actions.register_action("temple_test_action", lambda params: "fake-result")
    yield


# ── the full down-execute-up loop ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegate_internal_full_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology())

    from lhassa.notify import dispatch as notify_dispatch

    delivered_calls = []

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        delivered_calls.append(text)
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)

    cfg = _cfg(tmp_path)
    record = await temple.delegate_internal(
        "temple_test_action", {}, config=cfg, instruction="run the fake test action"
    )

    assert record == {
        "ok": True,
        "action": "temple_test_action",
        "result": "fake-result",
        "delivered": True,
    }
    assert delivered_calls  # the up-hop actually sent something

    # DOWN hop — one org-delegate episode.
    down = await _episodes("inst_personal", "org-delegate")
    assert len(down) == 1
    assert down[0]["accion"] == "delegate:lhassa→templo"
    assert "down to templo" in down[0]["decision"]
    assert float(down[0]["costo_usd"]) == 0.0

    # EXECUTE hop — one templo-execute episode, dominio="templo".
    exec_eps = await _episodes("inst_personal", "templo-execute")
    assert len(exec_eps) == 1
    assert exec_eps[0]["dominio"] == "templo"
    assert exec_eps[0]["resultado"] == "fake-result"
    assert exec_eps[0]["accion"] == "temple_test_action"

    # UP hop — org-deliver episodes, originating at templo.
    up = await _episodes("inst_personal", "org-deliver")
    assert up, "expected org-deliver episodes for the up hop"
    assert up[0]["dominio"] == "templo"


# ── degrade path: broken topology still executes ──────────────────────────────


@pytest.mark.asyncio
async def test_delegate_internal_degrades_on_broken_topology(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    def _boom(config=None):
        raise RuntimeError("topology unavailable")

    monkeypatch.setattr(topo_mod, "load_topology", _boom)

    from lhassa.notify import dispatch as notify_dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)

    cfg = _cfg(tmp_path)
    record = await temple.delegate_internal("temple_test_action", {}, config=cfg)

    # Governance never blocks the work: the action still ran, still reports ok.
    assert record["ok"] is True
    assert record["result"] == "fake-result"

    down = await _episodes("inst_personal", "org-delegate")
    assert down == []  # the down-hop episode itself was skipped
    exec_eps = await _episodes("inst_personal", "templo-execute")
    assert len(exec_eps) == 1  # execution still happened


# ── a failing action never raises ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegate_internal_survives_a_failing_action(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology())

    from lhassa.notify import dispatch as notify_dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)

    def _boom(params):
        raise RuntimeError("boom")

    core_actions.register_action("temple_boom_action", _boom, override=True)

    cfg = _cfg(tmp_path)
    record = await temple.delegate_internal("temple_boom_action", {}, config=cfg)

    assert record["ok"] is True  # actions.run already captures the error as text
    assert "errored" in record["result"]


# ── unknown action ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegate_internal_unknown_action(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology())

    from lhassa.notify import dispatch as notify_dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)

    cfg = _cfg(tmp_path)
    record = await temple.delegate_internal("does_not_exist", {}, config=cfg)

    assert record["ok"] is True
    assert "unknown action" in record["result"]


# ── worker resolution (the monks) ────────────────────────────────────────────────


def test_resolve_worker_maps_each_maintenance_action_to_its_monk():
    topo = _topology_with_workers()
    assert temple.resolve_worker("curate_skills", topo) == "guardian"
    assert temple.resolve_worker("review_tools", topo) == "guardian"
    assert temple.resolve_worker("distill_memory", topo) == "guardian"
    assert temple.resolve_worker("detect_repetition", topo) == "oraculo"
    assert temple.resolve_worker("calendar_sync", topo) == "sincronizador"


def test_resolve_worker_falls_back_to_templo_when_unmapped():
    topo = _topology_with_workers()
    assert temple.resolve_worker("some_unmapped_action", topo) == "templo"


def test_resolve_worker_falls_back_to_templo_when_worker_missing_from_topology():
    # Mapped to "guardian" in ACTION_OWNERS, but this topology has no workers.
    topo = _topology()
    assert temple.resolve_worker("curate_skills", topo) == "templo"


# ── the full chain: curate_skills routes through its owning monk (guardian) ─────


@pytest.mark.asyncio
async def test_delegate_internal_attributes_curate_skills_to_guardian(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology_with_workers())

    from lhassa.notify import dispatch as notify_dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)

    # Mock the real curate_skills action — nothing should actually spend/touch disk.
    core_actions.register_action(
        "curate_skills", lambda params: "3 stale skills archived", override=True
    )

    cfg = _cfg(tmp_path)
    record = await temple.delegate_internal("curate_skills", {}, config=cfg)

    assert record["ok"] is True
    assert record["result"] == "3 stale skills archived"

    # DOWN hop — the path names the owning monk.
    down = await _episodes("inst_personal", "org-delegate")
    assert len(down) == 1
    assert down[0]["accion"] == "delegate:lhassa→templo→guardian"
    assert "down to guardian" in down[0]["decision"]

    # EXECUTE hop — attributed to the monk, reporting up through templo.
    exec_eps = await _episodes("inst_personal", "templo-execute")
    assert len(exec_eps) == 1
    assert exec_eps[0]["dominio"] == "guardian"
    assert exec_eps[0]["accion"] == "curate_skills"
    assert "3 stale skills archived" in exec_eps[0]["resultado"]
    assert "reports ↑ templo" in exec_eps[0]["resultado"]

    # UP hop — walks guardian → templo → lhassa, one episode per brain.
    up = await _episodes("inst_personal", "org-deliver")
    dominios = [e["dominio"] for e in up]
    # One hop per brain on the chain (guardian produces, templo forwards,
    # lhassa receives) plus lhassa's own final send-confirmation episode.
    assert dominios == ["guardian", "templo", "lhassa", "lhassa"]


@pytest.mark.asyncio
async def test_delegate_internal_unmapped_action_still_attributes_to_templo(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology_with_workers())

    from lhassa.notify import dispatch as notify_dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)

    cfg = _cfg(tmp_path)
    record = await temple.delegate_internal("temple_test_action", {}, config=cfg)

    assert record["ok"] is True
    exec_eps = await _episodes("inst_personal", "templo-execute")
    assert exec_eps[-1]["dominio"] == "templo"

    up = await _episodes("inst_personal", "org-deliver")
    assert up[0]["dominio"] == "templo"


@pytest.mark.asyncio
async def test_delegate_internal_degrades_to_templo_when_workers_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.org import topology as topo_mod

    # A topology WITHOUT the worker brains — curate_skills is mapped to
    # "guardian" but it does not exist here, so it must degrade to templo.
    monkeypatch.setattr(topo_mod, "load_topology", lambda config=None: _topology())

    from lhassa.notify import dispatch as notify_dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)

    core_actions.register_action(
        "curate_skills", lambda params: "3 stale skills archived", override=True
    )

    cfg = _cfg(tmp_path)
    record = await temple.delegate_internal("curate_skills", {}, config=cfg)

    assert record["ok"] is True
    exec_eps = await _episodes("inst_personal", "templo-execute")
    assert exec_eps[-1]["dominio"] == "templo"  # degraded, still executed

    up = await _episodes("inst_personal", "org-deliver")
    assert up[0]["dominio"] == "templo"
