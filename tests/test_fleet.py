"""The fleet read-model (``lhassa.core.fleet``), the read ``/api/fleet`` and
``/fleet`` both serve. Offline: the brain DB is a tmp file."""

import pytest

from lhassa.config import Config
from lhassa.core import fleet


@pytest.mark.asyncio
async def test_list_fleet_unions_checkins_and_desired_state(tmp_path, monkeypatch):
    import lhassa.core.db as db
    from lhassa.core.episode import Episode

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    inst = Config.instance_id
    await db.upsert_desired_state(inst, "laptop", "repoA", "main")
    ep = Episode.begin(despertar_por="checkin:desk", instance_id=inst)
    ep.finish(decision="checkin", accion="checkin", resultado="ok")
    await ep.append()

    rows = await fleet.list_fleet()
    assert [r["machine_id"] for r in rows] == ["desk", "laptop"]
    desk, laptop = rows
    assert desk["last_checkin"] and desk["desired_state"] == {}
    assert laptop["last_checkin"] is None
    assert laptop["desired_state"] == {"repoA": "main"}


@pytest.mark.asyncio
async def test_list_fleet_empty(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    assert await fleet.list_fleet() == []
