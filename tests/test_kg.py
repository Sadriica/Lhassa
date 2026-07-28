"""Tests for lhassa.memory.kg — the persisted knowledge graph (Wave 3)."""

from types import SimpleNamespace

import pytest

from lhassa.memory import kg

INSTANCE = "inst_test"


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_kg.db")
    await db.init_db()
    return db


def _cfg(**over):
    base = dict(instance_id=INSTANCE, memory_graph_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


async def _make_episode(db, instance_id, despertar_por, resultado=""):
    from lhassa.core.episode import Episode

    ep = Episode.begin(despertar_por=despertar_por, instance_id=instance_id)
    ep.finish(decision="d", accion="a", resultado=resultado)
    await db.append_episode(ep)


# ── vocabulary + validation discipline (mirrors org/relationships.py) ────────


@pytest.mark.asyncio
async def test_add_edge_refuses_unknown_kind(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    a = await kg.upsert_entity(INSTANCE, "budget", when="2026-01-01T00:00:00")
    b = await kg.upsert_entity(INSTANCE, "visa", when="2026-01-01T00:00:00")
    with pytest.raises(kg.KGValidationError):
        await kg.add_edge(INSTANCE, a, b, "invented_kind")
    assert await db.kg_neighbors(INSTANCE, [a]) == []


@pytest.mark.asyncio
async def test_add_edge_refuses_self_edge(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    a = await kg.upsert_entity(INSTANCE, "budget", when="2026-01-01T00:00:00")
    with pytest.raises(kg.KGValidationError):
        await kg.add_edge(INSTANCE, a, a, kg.CO_OCCURS)


def test_edge_kinds_closed_vocabulary():
    assert kg.EDGE_KINDS == ("co_occurs", "mentions", "derived_from")
    assert kg.SYMMETRIC_KINDS == frozenset({"co_occurs"})


# ── canonical symmetric storage ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_co_occurs_is_stored_canonically_both_directions(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    a = await kg.upsert_entity(INSTANCE, "budget", when="2026-01-01T00:00:00")
    b = await kg.upsert_entity(INSTANCE, "visa", when="2026-01-01T00:00:00")

    await kg.add_edge(INSTANCE, a, b, kg.CO_OCCURS, when="2026-01-01T00:00:00")
    await kg.add_edge(INSTANCE, b, a, kg.CO_OCCURS, when="2026-01-01T00:01:00")

    edges = await db.kg_neighbors(INSTANCE, [a])
    assert len(edges) == 1  # A→B and B→A collapsed into ONE canonical row
    assert edges[0]["weight"] == pytest.approx(2.0)  # weight accumulated


@pytest.mark.asyncio
async def test_mentions_is_directed_not_collapsed(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    a = await kg.upsert_entity(INSTANCE, "Ana", kind=kg.ENTITY_TERM, when="2026-01-01T00:00:00")
    b = await kg.upsert_entity(INSTANCE, "work", kind=kg.ENTITY_TAG, when="2026-01-01T00:00:00")

    await kg.add_edge(INSTANCE, a, b, kg.MENTIONS, when="2026-01-01T00:00:00")
    edges = await db.kg_neighbors(INSTANCE, [a])
    assert len(edges) == 1
    assert edges[0]["from_id"] == a
    assert edges[0]["to_id"] == b


# ── weight accumulation via add_edge (not just canonical dedup) ─────────────


@pytest.mark.asyncio
async def test_weight_accumulates_on_repeated_edge(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    a = await kg.upsert_entity(INSTANCE, "budget", when="2026-01-01T00:00:00")
    b = await kg.upsert_entity(INSTANCE, "visa", when="2026-01-01T00:00:00")
    for _ in range(3):
        await kg.add_edge(INSTANCE, a, b, kg.CO_OCCURS, when="2026-01-01T00:00:00")
    edges = await db.kg_neighbors(INSTANCE, [a])
    assert edges[0]["weight"] == pytest.approx(3.0)


# ── sync() — the $0 feeder, no provider call ─────────────────────────────────


@pytest.mark.asyncio
async def test_sync_is_noop_when_flag_disabled(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts

    await facts.add_fact(INSTANCE, "dato", "Ana works at Sezzle", tags=["work"])
    cfg = _cfg(memory_graph_enabled=False)
    result = await kg.sync(INSTANCE, config=cfg)
    assert result == {"entities": 0, "edges": 0, "scanned": 0}
    assert await db.list_kg_entities(INSTANCE) == []


@pytest.mark.asyncio
async def test_sync_populates_entities_and_edges_from_facts_and_episodes(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts

    await facts.add_fact(INSTANCE, "dato", "Ana works at Sezzle", tags=["work"])
    await _make_episode(db, INSTANCE, "checkin", resultado="Ana mentioned Sezzle again")

    # Assert the routine think path is NEVER touched — extraction is $0 regex.
    import lhassa.llm.provider as provider_mod

    calls: list = []

    def _boom(*a, **kw):
        calls.append((a, kw))
        raise AssertionError("provider must never be called by kg.sync")

    monkeypatch.setattr(provider_mod.PROVIDERS, "get", _boom)
    monkeypatch.setattr(provider_mod.PROVIDERS, "get_or_none", _boom)

    cfg = _cfg()
    result = await kg.sync(INSTANCE, config=cfg)

    assert calls == []  # the provider registry was never consulted
    assert result["scanned"] == 2  # 1 fact + 1 episode
    assert result["entities"] >= 3  # Ana, Sezzle, work (at least)
    assert result["edges"] > 0

    entities = {e["name"]: e for e in await db.list_kg_entities(INSTANCE)}
    assert "Ana" in entities
    assert "Sezzle" in entities
    assert "work" in entities
    assert entities["work"]["kind"] == kg.ENTITY_TAG
    assert entities["Ana"]["kind"] == kg.ENTITY_TERM


@pytest.mark.asyncio
async def test_sync_never_raises_on_internal_failure(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts

    await facts.add_fact(INSTANCE, "dato", "Ana works at Sezzle", tags=["work"])

    async def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(kg, "upsert_entity", _boom)
    cfg = _cfg()
    result = await kg.sync(INSTANCE, config=cfg)
    assert result == {"entities": 0, "edges": 0, "scanned": 0}


# ── incremental sync via the watch_state marker ──────────────────────────────


@pytest.mark.asyncio
async def test_sync_is_incremental_via_marker(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts

    await facts.add_fact(INSTANCE, "dato", "Ana works at Sezzle", tags=["work"])
    cfg = _cfg()

    first = await kg.sync(INSTANCE, config=cfg)
    assert first["scanned"] == 1

    # Nothing new — a second pass immediately after must scan nothing.
    second = await kg.sync(INSTANCE, config=cfg)
    assert second == {"entities": 0, "edges": 0, "scanned": 0}

    marker = await db.get_watch_state(INSTANCE, kg._SYNC_MARKER_KEY)
    assert marker  # advanced past the empty string

    await facts.add_fact(INSTANCE, "dato", "Carla lives in Bogota", tags=["family"])
    third = await kg.sync(INSTANCE, config=cfg)
    assert third["scanned"] == 1  # only the NEW fact, not the first one again

    entities = {e["name"] for e in await db.list_kg_entities(INSTANCE)}
    assert "Carla" in entities
    assert "Bogota" in entities
