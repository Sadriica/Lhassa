"""Tests for lhassa.core.episode; the structured Episode."""

import json

import pytest
import pytest_asyncio  # noqa: F401


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


# ── begin / finish ──────────────────────────────────────────────────────────────

def test_begin_stamps_id_and_timestamp():
    from lhassa.core.episode import Episode
    ep = Episode.begin(despertar_por="heartbeat", instance_id="inst_personal")
    assert ep.id.startswith("ep_")
    assert ep.instance_id == "inst_personal"
    assert ep.despertar_por == "heartbeat"
    assert ep.creado_en  # non-empty ISO timestamp
    assert ep.memoria_cargada == []
    assert ep.opciones == []


def test_finish_sets_outcome_and_chains():
    from lhassa.core.episode import Episode
    ep = Episode.begin(despertar_por="heartbeat", instance_id="inst_personal")
    returned = ep.finish(decision="d", accion="a", resultado="r", costo_usd=0.5)
    assert returned is ep
    assert (ep.decision, ep.accion, ep.resultado, ep.costo_usd) == ("d", "a", "r", 0.5)


# ── round-trip begin → finish → append → load ───────────────────────────────────

@pytest.mark.asyncio
async def test_episode_round_trips(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core.episode import Episode

    ep = Episode.begin(despertar_por="pc_reporto", instance_id="inst_personal")
    ep.memoria_cargada = ["fact_1:pinned", "ep_99:recencia"]
    ep.opciones = ["sync_repo", "esperar"]
    ep.finish(decision="sync_repo", accion="git pull", resultado="ff a def456", costo_usd=0.02)
    await ep.append()

    rows = await db.load_recent_episodes("inst_personal")
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == ep.id
    assert row["instance_id"] == "inst_personal"
    assert row["despertar_por"] == "pc_reporto"
    assert row["decision"] == "sync_repo"
    assert row["accion"] == "git pull"
    assert row["resultado"] == "ff a def456"
    assert row["costo_usd"] == 0.02
    assert row["memoria_cargada"] == ["fact_1:pinned", "ep_99:recencia"]

    # opciones survive in the full JSON copy (raw)
    raw = json.loads(row["raw"])
    assert raw["opciones"] == ["sync_repo", "esperar"]
    assert raw["memoria_cargada"] == ["fact_1:pinned", "ep_99:recencia"]


@pytest.mark.asyncio
async def test_append_via_episode_method_filters_by_instance(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core.episode import Episode

    a = Episode.begin(despertar_por="x", instance_id="inst_a")
    b = Episode.begin(despertar_por="y", instance_id="inst_b")
    await a.append()
    await b.append()

    rows_b = await db.load_recent_episodes("inst_b")
    assert len(rows_b) == 1
    assert rows_b[0]["despertar_por"] == "y"
