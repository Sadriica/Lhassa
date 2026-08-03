"""Temple wiring for ``ingest_knowledge`` — document perception.

Offline + $0. Trusts tests/test_org_temple.py for the generic down-execute-up
loop mechanics — this file only asserts ingest_knowledge's OWN wiring: it's in
the closed TEMPLE_ACTIONS set, resolve_worker attributes it to synchronizer,
and the /temple command's keyword matcher routes to it without shadowing
calendar_sync.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.actionpack._temple import TEMPLE_ACTIONS
from lhassa.core import db
from lhassa.notify import commands
from lhassa.org import temple
from lhassa.org.topology import BrainNode, Topology


def test_ingest_knowledge_is_in_the_closed_action_set():
    assert "ingest_knowledge" in TEMPLE_ACTIONS


def test_resolve_worker_attributes_ingest_knowledge_to_synchronizer():
    nodes = [
        BrainNode(id="lhassa", role="administrator"),
        BrainNode(id="temple", role="internal-operations", department="temple", parent="lhassa"),
    ]
    for worker_id in temple.TEMPLE_WORKERS:
        nodes.append(BrainNode(id=worker_id, role="monk", department="temple", parent="temple"))
    topology = Topology(nodes)

    assert temple.resolve_worker("ingest_knowledge", topology) == temple.SYNCHRONIZER


def test_resolve_worker_degrades_to_temple_when_synchronizer_missing():
    topology = Topology(
        [
            BrainNode(id="lhassa", role="administrator"),
            BrainNode(id="temple", role="internal-operations", department="temple", parent="lhassa"),
        ]
    )
    assert temple.resolve_worker("ingest_knowledge", topology) == temple.TEMPLE_DEPARTMENT


@pytest.mark.parametrize("text", ["sync my notes", "sync knowledge", "index my notes", "knowledge base"])
def test_temple_keyword_matcher_routes_to_ingest_knowledge(text):
    assert commands._match_temple_action(text) == "ingest_knowledge"


def test_temple_keyword_calendar_sync_still_routes_correctly():
    # The new knowledge keywords must not shadow the existing calendar routing.
    assert commands._match_temple_action("sync my calendar") == "calendar_sync"
    assert commands._match_temple_action("sync") == "calendar_sync"


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
        knowledge_roots="",
        knowledge_max_file_kb=512,
        knowledge_chunk_chars=2000,
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


def test_temple_knowledge_door_dispatches(tmp_path):
    reply = asyncio.run(commands.dispatch("/temple sync my notes", config=_cfg(tmp_path)))
    assert "ingest_knowledge" in reply
    assert "no knowledge roots set" in reply
