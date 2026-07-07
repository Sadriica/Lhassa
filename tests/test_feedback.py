"""Tests for the $0 feedback signal (lhassa.core.feedback).

Seeds a tmp brain.db with episode sequences that exercise each heuristic —
re-ask, correction, silence-after, acted-on — plus the summary aggregation and
the API read surface. No LLM, no network; everything is direct SQLite.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lhassa.api import analytics_routes
from lhassa.core import feedback


@dataclass
class _FakeEpisode:
    """Minimal Episode double — just enough for db.append_episode."""

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


@pytest_asyncio.fixture
async def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


async def _seed_episode(
    db,
    *,
    instance_id="inst_test",
    despertar_por="telegram",
    accion="responder",
    decision="",
    resultado="",
    cost=0.0,
    when,
):
    ep = _FakeEpisode(
        id=db._new_id("ep"),
        instance_id=instance_id,
        despertar_por=despertar_por,
        accion=accion,
        decision=decision,
        resultado=resultado,
        costo_usd=cost,
        creado_en=when,
    )
    await db.append_episode(ep)
    return ep.id


async def _seed_approval(db, *, instance_id="inst_test", tipo="code_run", estado="aprobada", when):
    """Insert an approval row with an EXPLICIT ``creado_en`` (db.add_approval
    always stamps "now", which can't exercise the acted-on time-window logic)."""
    from lhassa._vendor import aiosqlite

    approval_id = db._new_id("ap")
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO approvals(id, instance_id, tipo, payload, estado, creado_en)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (approval_id, instance_id, tipo, "{}", estado, when),
        )
        await conn.commit()
    return approval_id


def _t(minute: int, hour: int = 10) -> str:
    """A timestamp anchored to "yesterday" (always inside a 7-day lookback,
    regardless of when the test runs), offset by ``minute`` from ``hour``."""
    anchor = datetime.now(timezone.utc) - timedelta(days=1)
    return anchor.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


# ── heuristic: re-ask (negative) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reask_signal_is_negative(brain):
    inst = "inst_test"
    first = await _seed_episode(
        brain,
        instance_id=inst,
        decision="think: provider=opencode model=x",
        resultado="It's 18°C in Santiago.",
        when=_t(0),
    )
    await _seed_episode(
        brain,
        instance_id=inst,
        decision="cache-hit: served past answer (sim=0.97)",
        resultado="It's 18°C in Santiago.",
        when=_t(20),
    )

    signals = await feedback.derive_signals(inst, days=7)
    by_id = {s["episode_id"]: s for s in signals}
    assert by_id[first]["signal"] == "negative"
    assert "re-ask" in by_id[first]["reason"]
    # The cache-hit episode itself is not separately scored.
    assert len(signals) == 1


# ── heuristic: correction (negative) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_correction_signal_is_negative(brain):
    inst = "inst_test"
    first = await _seed_episode(
        brain,
        instance_id=inst,
        decision="think: provider=opencode model=x",
        resultado="Your flight leaves at 8am.",
        when=_t(0),
    )
    await _seed_episode(
        brain,
        instance_id=inst,
        decision="No, en realidad quería el vuelo de las 9am.",
        resultado="Got it, correcting that.",
        when=_t(5),
    )

    signals = await feedback.derive_signals(inst, days=7)
    by_id = {s["episode_id"]: s for s in signals}
    assert by_id[first]["signal"] == "negative"
    assert "correction" in by_id[first]["reason"]


# ── heuristic: silence-after (weak positive) ────────────────────────────────


@pytest.mark.asyncio
async def test_silence_after_signal_is_weak_positive(brain):
    inst = "inst_test"
    first = await _seed_episode(
        brain,
        instance_id=inst,
        decision="think: provider=opencode model=x",
        resultado="Done — meeting moved to 3pm.",
        when=_t(0),
    )
    # A later, UNRELATED answer far past the silence window — no re-ask, no
    # correction, no action: the gap alone should read as weak positive.
    await _seed_episode(
        brain,
        instance_id=inst,
        decision="think: provider=opencode model=x",
        resultado="Sure, the weather tomorrow looks clear.",
        when=_t(30, hour=14),
    )

    signals = await feedback.derive_signals(inst, days=7)
    by_id = {s["episode_id"]: s for s in signals}
    assert by_id[first]["signal"] == "positive"
    assert "silence-after" in by_id[first]["reason"]
    assert by_id[first]["confidence"] < 0.7  # weak, not strong


# ── heuristic: acted-on (positive) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_acted_on_signal_is_positive_via_approval(brain):
    inst = "inst_test"
    first = await _seed_episode(
        brain,
        instance_id=inst,
        decision="think: provider=opencode model=x",
        resultado="Parked a code run for approval.",
        when=_t(0),
    )
    await _seed_approval(brain, instance_id=inst, estado="aprobada", when=_t(2))

    signals = await feedback.derive_signals(inst, days=7)
    by_id = {s["episode_id"]: s for s in signals}
    assert by_id[first]["signal"] == "positive"
    assert "acted-on" in by_id[first]["reason"]


@pytest.mark.asyncio
async def test_acted_on_signal_is_positive_via_followup_action(brain):
    inst = "inst_test"
    first = await _seed_episode(
        brain,
        instance_id=inst,
        decision="think: provider=opencode model=x",
        resultado="Sure, I'll remind you.",
        when=_t(0),
    )
    await _seed_episode(
        brain,
        instance_id=inst,
        despertar_por="telegram",
        accion="remind",
        decision="remind: set",
        resultado="Reminder set for 5pm.",
        when=_t(3),
    )

    signals = await feedback.derive_signals(inst, days=7)
    by_id = {s["episode_id"]: s for s in signals}
    assert by_id[first]["signal"] == "positive"
    assert "acted-on" in by_id[first]["reason"]


# ── neutral / degenerate ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recent_answer_with_no_followup_is_neutral(brain):
    """An answer that just happened (no time has passed) is inconclusive."""
    inst = "inst_test"
    now = datetime.now(timezone.utc).isoformat()
    first = await _seed_episode(
        brain,
        instance_id=inst,
        decision="think: provider=opencode model=x",
        resultado="Here's the summary you asked for.",
        when=now,
    )
    signals = await feedback.derive_signals(inst, days=7)
    by_id = {s["episode_id"]: s for s in signals}
    assert by_id[first]["signal"] == "neutral"


@pytest.mark.asyncio
async def test_empty_log_returns_empty_list(brain):
    signals = await feedback.derive_signals("inst_nobody", days=7)
    assert signals == []


@pytest.mark.asyncio
async def test_derive_signals_never_raises_on_bad_db_path(monkeypatch, tmp_path):
    import lhassa.core.db as db

    # Point at a path whose parent doesn't exist and can't be created implicitly
    # by a plain connect with no init_db() call — exercise the try/except.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "nested" / "missing" / "brain.db")
    signals = await feedback.derive_signals("inst_x", days=7)
    assert signals == []


# ── summary() aggregation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_aggregates_and_scores(brain):
    inst = "inst_test"
    # One negative (re-ask), one positive (acted-on).
    await _seed_episode(
        brain,
        instance_id=inst,
        resultado="Same answer.",
        when=_t(0),
    )
    await _seed_episode(
        brain,
        instance_id=inst,
        decision="cache-hit: served past answer (sim=0.99)",
        resultado="Same answer.",
        when=_t(10),
    )
    await _seed_episode(
        brain,
        instance_id=inst,
        resultado="Booked the table.",
        when=_t(40),
    )
    await _seed_approval(brain, instance_id=inst, estado="aprobada", when=_t(41))

    result = await feedback.summary(inst, days=7)
    assert result["negative"] == 1
    assert result["positive"] == 1
    assert result["total"] == 2
    assert result["net_score"] == 0.0


@pytest.mark.asyncio
async def test_summary_empty_log_never_raises(brain):
    result = await feedback.summary("inst_nobody", days=7)
    assert result["total"] == 0
    assert result["net_score"] == 0.0


# ── API read surface ────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    import lhassa.config as config_mod
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain_api.db")
    monkeypatch.setattr(config_mod.Config, "instance_id", "inst_test")

    app = FastAPI()
    app.include_router(analytics_routes.router)
    return TestClient(app)


def test_feedback_route_returns_summary_shape(client):
    response = client.get("/api/analytics/feedback")
    assert response.status_code == 200
    body = response.json()
    for key in ("positive", "negative", "neutral", "total", "net_score", "range_days"):
        assert key in body


def test_feedback_route_never_500s_on_empty_db(client):
    response = client.get("/api/analytics/feedback?range=30d")
    assert response.status_code == 200
    assert response.json()["total"] == 0
