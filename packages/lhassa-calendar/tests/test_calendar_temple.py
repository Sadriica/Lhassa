"""The ``/temple derive`` door runs the calendar pack's ``derive_world_facts``.

Split from core's ``tests/test_derive_world_facts_temple.py`` (which keeps the
pack-independent wiring: the closed action set, guardian attribution and the
keyword matcher): actually dispatching the action needs the pack installed.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.core import db
from lhassa.notify import commands


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
