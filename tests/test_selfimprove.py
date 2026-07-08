"""Tests for lhassa.actionpack._selfimprove — detect_repetition, review_tools.

Offline: real tmp brain DB, notify mocked (no network/desktop), approvals go
through the real security.approval queue against the tmp DB.
"""

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.actionpack._selfimprove import detect_repetition, review_tools


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


@pytest_asyncio.fixture
async def brain(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


async def _seed_pattern(db, instance_id, n=6, cost=0.01):
    for _ in range(n):
        ep = _FakeEpisode(
            id=db._new_id("ep"),
            instance_id=instance_id,
            despertar_por="pending_task",
            accion="think",
            costo_usd=cost,
            creado_en=db._now(),
        )
        await db.append_episode(ep)


# ── detect_repetition ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detect_repetition_proposes_new_pattern(brain):
    await _seed_pattern(brain, "inst_test")
    with patch(
        "lhassa.actionpack._selfimprove._notify", AsyncMock(return_value=True)
    ) as mock_notify:
        result = await detect_repetition({"instance_id": "inst_test", "days": 7})

    assert "1 pattern" in result
    assert "1 newly proposed" in result
    mock_notify.assert_awaited()

    pending = await brain.load_pending_approvals("inst_test")
    assert any(a["tipo"] == "mint_action" for a in pending)


@pytest.mark.asyncio
async def test_detect_repetition_does_not_reprose_same_pattern(brain):
    await _seed_pattern(brain, "inst_test")
    with patch("lhassa.actionpack._selfimprove._notify", AsyncMock(return_value=True)):
        await detect_repetition({"instance_id": "inst_test"})
        # A second run over the SAME (still-repeating) pattern must not park
        # a second approval — watch_state dedup.
        result2 = await detect_repetition({"instance_id": "inst_test"})

    assert "0 newly proposed" in result2
    pending = await brain.load_pending_approvals("inst_test")
    mint_action_rows = [a for a in pending if a["tipo"] == "mint_action"]
    assert len(mint_action_rows) == 1


@pytest.mark.asyncio
async def test_detect_repetition_no_patterns(brain):
    result = await detect_repetition({"instance_id": "inst_test"})
    assert "no repeated patterns" in result


# ── the consciousness: mint_memory / mint_schedule / seed_cache ──────────────────


async def _seed_judgment_pattern(db, instance_id, *, despertar_por="telegram", accion="responder", n=4):
    for _ in range(n):
        ep = _FakeEpisode(
            id=db._new_id("ep"),
            instance_id=instance_id,
            despertar_por=despertar_por,
            accion=accion,
            costo_usd=0.0,
            creado_en=db._now(),
        )
        await db.append_episode(ep)


@pytest.mark.asyncio
async def test_detect_repetition_proposes_mint_memory_for_a_judgment_pattern(brain):
    await _seed_judgment_pattern(brain, "inst_test")
    with patch(
        "lhassa.actionpack._selfimprove._notify", AsyncMock(return_value=True)
    ) as mock_notify:
        result = await detect_repetition({"instance_id": "inst_test", "days": 7})

    assert "newly proposed" in result
    mock_notify.assert_awaited()
    pending = await brain.load_pending_approvals("inst_test")
    mint_memory_rows = [a for a in pending if a["tipo"] == "mint_memory"]
    assert len(mint_memory_rows) == 1
    assert mint_memory_rows[0]["payload"]["despertar_por"] == "telegram"


@pytest.mark.asyncio
async def test_detect_repetition_does_not_reproprose_mint_memory(brain):
    await _seed_judgment_pattern(brain, "inst_test")
    with patch("lhassa.actionpack._selfimprove._notify", AsyncMock(return_value=True)):
        await detect_repetition({"instance_id": "inst_test"})
        result2 = await detect_repetition({"instance_id": "inst_test"})

    assert "0 newly proposed" in result2
    pending = await brain.load_pending_approvals("inst_test")
    assert len([a for a in pending if a["tipo"] == "mint_memory"]) == 1


@pytest.mark.asyncio
async def test_detect_repetition_proposes_mint_schedule_for_a_cadence(brain):
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc) - timedelta(days=28)
    for i in range(5):
        ep = _FakeEpisode(
            id=brain._new_id("ep"),
            instance_id="inst_test",
            despertar_por="weekly_report",
            accion="think",
            costo_usd=0.0,
            creado_en=(base + timedelta(days=7 * i)).isoformat(),
        )
        await brain.append_episode(ep)

    with patch("lhassa.actionpack._selfimprove._notify", AsyncMock(return_value=True)):
        await detect_repetition({"instance_id": "inst_test", "days": 60})

    pending = await brain.load_pending_approvals("inst_test")
    mint_schedule_rows = [a for a in pending if a["tipo"] == "mint_schedule"]
    assert len(mint_schedule_rows) == 1
    assert mint_schedule_rows[0]["payload"]["interval_days"] == pytest.approx(7.0, abs=0.1)


@pytest.mark.asyncio
async def test_detect_repetition_proposes_seed_cache_for_a_cache_hit_pattern(brain):
    cache_id = await brain.insert_answer_cache(
        {
            "instance_id": "inst_test",
            "question": "what is the capital of France",
            "answer": "Paris",
            "embedding": [0.1, 0.2, 0.3],
            "tags": [],
        }
    )
    for _ in range(3):
        await brain.bump_answer_cache_hit("inst_test", cache_id)

    with patch("lhassa.actionpack._selfimprove._notify", AsyncMock(return_value=True)):
        await detect_repetition({"instance_id": "inst_test", "days": 7})

    pending = await brain.load_pending_approvals("inst_test")
    seed_cache_rows = [a for a in pending if a["tipo"] == "seed_cache"]
    assert len(seed_cache_rows) == 1
    assert seed_cache_rows[0]["payload"]["question"] == "what is the capital of France"


# ── review_tools ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_tools_retires_unused_graduated_tool(brain):
    await brain.upsert_tool("inst_test", "unused_tool", estado="graduada")
    result = await review_tools({"instance_id": "inst_test"})
    assert "retired 1/1" in result

    row = await brain.get_tool("inst_test", "unused_tool")
    assert row["estado"] == "cuarentena"


@pytest.mark.asyncio
async def test_review_tools_keeps_healthy_tool(brain):
    tool_id = await brain.upsert_tool("inst_test", "healthy_tool", estado="graduada")
    assert tool_id
    for _ in range(10):
        await brain.bump_tool_usage("inst_test", "healthy_tool", ok=True)

    result = await review_tools({"instance_id": "inst_test"})
    assert "none retired" in result

    row = await brain.get_tool("inst_test", "healthy_tool")
    assert row["estado"] == "graduada"


@pytest.mark.asyncio
async def test_review_tools_no_tools(brain):
    result = await review_tools({"instance_id": "inst_test"})
    assert "0 tool(s) checked" in result


# ── the self-routing consciousness: mint_route ───────────────────────────────


async def _seed_routable_pattern(db, instance_id, *, capability="calendar_view", n=6):
    for _ in range(n):
        ep = _FakeEpisode(
            id=db._new_id("ep"),
            instance_id=instance_id,
            despertar_por="telegram",
            accion="responder",
            decision=f"think: provider=x model=y [routable:{capability}]",
            costo_usd=0.01,
            creado_en=db._now(),
        )
        await db.append_episode(ep)


@pytest.mark.asyncio
async def test_detect_repetition_proposes_mint_route_for_a_routable_pattern(brain):
    await _seed_routable_pattern(brain, "inst_test")
    with patch(
        "lhassa.actionpack._selfimprove._notify", AsyncMock(return_value=True)
    ) as mock_notify:
        result = await detect_repetition({"instance_id": "inst_test", "days": 7})

    assert "newly proposed" in result
    mock_notify.assert_awaited()
    pending = await brain.load_pending_approvals("inst_test")
    mint_route_rows = [a for a in pending if a["tipo"] == "mint_route"]
    assert len(mint_route_rows) == 1
    assert mint_route_rows[0]["payload"]["capability"] == "calendar_view"
    assert mint_route_rows[0]["payload"]["count"] == 6


@pytest.mark.asyncio
async def test_detect_repetition_does_not_reprose_mint_route(brain):
    await _seed_routable_pattern(brain, "inst_test")
    with patch("lhassa.actionpack._selfimprove._notify", AsyncMock(return_value=True)):
        await detect_repetition({"instance_id": "inst_test"})
        result2 = await detect_repetition({"instance_id": "inst_test"})

    assert "0 newly proposed" in result2
    pending = await brain.load_pending_approvals("inst_test")
    assert len([a for a in pending if a["tipo"] == "mint_route"]) == 1
