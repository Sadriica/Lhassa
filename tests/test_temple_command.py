"""Tests for the /temple Telegram command — the owner's door into templo.

Offline + $0: a tmp brain DB, a seeded lhassa->templo topology, and a fake
``notify.dispatch.notify`` so the up-hop send never touches the network.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.core import db
from lhassa.notify import commands


def _cfg(tmp_path, **over):
    topo = tmp_path / "org.json"
    topo.write_text(
        '[{"id":"templo","role":"internal-operations","department":"templo","parent":null}]',
        encoding="utf-8",
    )
    base = dict(
        instance_id="inst_personal",
        org_topology_file=str(topo),
        root_admin_id="lhassa",
        notify_channels="log",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from lhassa import actionpack

    actionpack.register_all()  # org_run/temple_run must be registered for mint_task
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())

    from lhassa.notify import dispatch as notify_dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)


def test_temple_requires_text(tmp_path):
    reply = asyncio.run(commands.dispatch("/temple", config=_cfg(tmp_path)))
    assert "Usage" in reply


def test_temple_keyword_match_runs_deterministic_action(tmp_path, monkeypatch):
    from lhassa.org import topology as topo_mod
    from lhassa.org.topology import BrainNode, Topology

    monkeypatch.setattr(
        topo_mod,
        "load_topology",
        lambda config=None: Topology(
            [
                BrainNode(id="lhassa", role="administrator"),
                BrainNode(id="templo", role="internal-operations", department="templo", parent="lhassa"),
            ]
        ),
    )

    reply = asyncio.run(
        commands.dispatch("/temple distill recent episodes into facts", config=_cfg(tmp_path))
    )
    assert "templo ran distill_memory" in reply

    rows = asyncio.run(db.load_recent_episodes("inst_personal", n=50))
    assert any(r["despertar_por"] == "templo-execute" for r in rows)


def test_temple_free_form_instruction_mints_org_run(tmp_path):
    reply = asyncio.run(
        commands.dispatch("/temple draft the internal onboarding doc", config=_cfg(tmp_path))
    )
    assert "Handed to templo" in reply

    tasks = asyncio.run(db.list_tasks("inst_personal", estado="pendiente"))
    org_runs = [t for t in tasks if t["accion"] == "org_run"]
    assert len(org_runs) == 1
    assert org_runs[0]["params"]["department"] == "templo"
