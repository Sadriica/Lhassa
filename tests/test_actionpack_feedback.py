"""Tests for the ``apply_feedback`` action (lhassa/actionpack/_feedback.py).

Offline, no network: a tmp brain DB seeded the same way as
tests/test_autonomy_feedback.py, matching the actionpack test pattern in
tests/test_actionpack_knowledge.py.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lhassa.actionpack import _feedback
from lhassa.core import db
from lhassa.security import autonomy
from lhassa.security.trust import TrustLevel


@dataclass
class _FakeEpisode:
    id: str
    instance_id: str
    despertar_por: str
    accion: str
    decision: str = ""
    resultado: str = ""
    costo_usd: float = 0.0
    creado_en: str = ""
    memoria_cargada: list = field(default_factory=list)
    opciones: list = field(default_factory=list)
    dominio: str = ""


@pytest.fixture
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    return db


def _cfg(**over):
    base = dict(
        instance_id="inst_personal",
        autonomy_threshold=3,
        always_approve_actions={"move_money", "delete_data"},
    )
    base.update(over)
    return SimpleNamespace(**base)


def _t(minute: int, hour: int = 9) -> str:
    anchor = datetime.now(timezone.utc) - timedelta(days=1)
    return anchor.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


async def _seed_episode(accion="responder", decision="", resultado="", when=""):
    ep = _FakeEpisode(
        id=db._new_id("ep"),
        instance_id="inst_personal",
        despertar_por="telegram",
        accion=accion,
        decision=decision,
        resultado=resultado,
        creado_en=when,
    )
    await db.append_episode(ep)
    return ep.id


async def _seed_reset_scenario():
    await autonomy.record_decision(
        "inst_personal", "sync_repo", "duenio", approved=True, origen_trust=int(TrustLevel.HUMANO)
    )
    await autonomy.record_decision(
        "inst_personal", "sync_repo", "duenio", approved=True, origen_trust=int(TrustLevel.HUMANO)
    )
    await _seed_episode(accion="sync_repo", when=_t(0, hour=9))
    await _seed_episode(accion="responder", resultado="pulled", when=_t(5, hour=9))
    await _seed_episode(accion="responder", resultado="No, wrong", when=_t(32, hour=9))
    await _seed_episode(accion="sync_repo", when=_t(0, hour=11))
    await _seed_episode(accion="responder", resultado="pulled X", when=_t(5, hour=11))
    await _seed_episode(
        accion="responder",
        decision="cache-hit: served past answer (sim=0.95)",
        resultado="pulled X",
        when=_t(35, hour=11),
    )


def test_no_signals_is_a_clean_message(_db):
    out = asyncio.run(_feedback.apply_feedback({"config": _cfg()}))
    assert "apply_feedback" in out
    assert "no scorable signals" in out


def test_reports_reset_accion(_db):
    asyncio.run(_seed_reset_scenario())
    out = asyncio.run(_feedback.apply_feedback({"config": _cfg()}))
    assert "reset 1 accion" in out
    assert "sync_repo" in out


def test_never_raises_on_bad_params():
    out = asyncio.run(_feedback.apply_feedback("not a dict"))  # type: ignore[arg-type]
    assert "apply_feedback" in out


def test_never_raises_when_autonomy_errors(_db, monkeypatch):
    from lhassa.security import autonomy as autonomy_mod

    async def boom(instance_id, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(autonomy_mod, "apply_feedback", boom)
    out = asyncio.run(_feedback.apply_feedback({"config": _cfg()}))
    assert "apply_feedback" in out
    assert "RuntimeError" in out
