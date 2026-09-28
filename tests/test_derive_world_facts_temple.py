"""Temple wiring for ``derive_world_facts``: the calendar-derived world model.

Offline + $0. Trusts tests/test_org_temple.py for the generic down-execute-up
loop mechanics (a topology failure degrading to a direct run, dominio
attribution, etc.); this file only asserts derive_world_facts' OWN wiring:
it's in the closed TEMPLE_ACTIONS set, resolve_worker attributes it to
guardian, and the /temple command's keyword matcher + door route to it.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.actionpack._temple import TEMPLE_ACTIONS
from lhassa.core import db
from lhassa.notify import commands
from lhassa.org import temple
from lhassa.org.topology import BrainNode, Topology


def test_derive_world_facts_is_in_the_closed_action_set():
    assert "derive_world_facts" in TEMPLE_ACTIONS


def test_resolve_worker_attributes_derive_to_guardian():
    nodes = [
        BrainNode(id="lhassa", role="administrator"),
        BrainNode(id="temple", role="internal-operations", department="temple", parent="lhassa"),
    ]
    for worker_id in temple.TEMPLE_WORKERS:
        nodes.append(BrainNode(id=worker_id, role="monk", department="temple", parent="temple"))
    topology = Topology(nodes)

    assert temple.resolve_worker("derive_world_facts", topology) == temple.GUARDIAN


def test_resolve_worker_degrades_to_temple_when_guardian_missing():
    topology = Topology(
        [
            BrainNode(id="lhassa", role="administrator"),
            BrainNode(id="temple", role="internal-operations", department="temple", parent="lhassa"),
        ]
    )
    assert temple.resolve_worker("derive_world_facts", topology) == temple.TEMPLE_DEPARTMENT


@pytest.mark.parametrize("text", ["derive", "derive my world", "learn calendar", "world model"])
def test_temple_keyword_matcher_routes_to_derive(text):
    assert commands._match_temple_action(text) == "derive_world_facts"


def test_temple_keyword_calendar_alone_still_routes_to_sync():
    # A bare "calendar"/"sync" phrase must keep routing to calendar_sync; the
    # new derive keywords must not shadow the existing behavior.
    assert commands._match_temple_action("sync my calendar") == "calendar_sync"


def _cfg(tmp_path):
    topo = tmp_path / "org.json"
    topo.write_text(
        '[{"id":"temple","role":"internal-operations","department":"temple","parent":null}]',
        encoding="utf-8",
    )
    return SimpleNamespace(
        instance_id="inst_personal",
        org_topology_file=str(topo),
        root_admin_id="lhassa",
        notify_channels="log",
    )


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from lhassa import actionpack

    actionpack.register_all()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())

    from lhassa.notify import dispatch as notify_dispatch

    async def fake_notify(text, *, level="info", config=None, **kwargs):
        return True

    monkeypatch.setattr(notify_dispatch, "notify", fake_notify)


def test_temple_derive_door_dispatches(tmp_path, monkeypatch):
    async def fake_derive(instance_id, *, config=None, days=45):
        return []

    from lhassa.calendar import derive as derive_mod

    monkeypatch.setattr(derive_mod, "derive_world", fake_derive)

    reply = asyncio.run(commands.dispatch("/temple derive", config=_cfg(tmp_path)))
    assert "derive_world_facts" in reply
    assert "nothing to derive" in reply
