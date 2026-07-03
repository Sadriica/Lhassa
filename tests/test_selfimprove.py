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
