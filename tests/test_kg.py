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
    assert kg.EDGE_KINDS == ("co_occurs", "mentions", "derived_from", "asserts")
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


# ── C1: opt-in cheap-model triple extraction ─────────────────────────────────


class _FakeTripleProvider:
    """Records the model/messages of each call; replies with canned JSON text."""

    def __init__(self, text: str = "[]", *, raises: Exception | None = None):
        self.name = "fake"
        self.zero_retention = False
        self._text = text
        self._raises = raises
        self.calls: list = []

    async def complete(self, messages, *, model=None, max_tokens=1024, **opts):
        from lhassa.llm.provider import Completion

        self.calls.append((model, messages))
        if self._raises is not None:
            raise self._raises
        return Completion(text=self._text, model=model or "?", cost_usd=0.0)


async def _asserts_edges(db, instance_id):
    entities = await db.list_kg_entities(instance_id)
    ids = [e["id"] for e in entities]
    if not ids:
        return []
    edges = await db.kg_neighbors(instance_id, ids, limit=200)
    return [e for e in edges if e["kind"] == kg.ASSERTS]


@pytest.mark.asyncio
async def test_sync_triples_flag_off_is_regression_locked(tmp_path, monkeypatch):
    """kg_triples_enabled defaults False — sync must never touch a provider,
    even if one is explicitly injected, and must write no ``asserts`` edges."""
    db = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts

    await facts.add_fact(INSTANCE, "dato", "Ana works at Sezzle", tags=["work"])
    fake = _FakeTripleProvider(text="[]")
    cfg = _cfg(kg_triples_enabled=False)

    result = await kg.sync(INSTANCE, config=cfg, provider=fake, model="cheap")

    assert fake.calls == []  # never invoked — the flag was off
    assert result["scanned"] == 1
    assert await _asserts_edges(db, INSTANCE) == []


@pytest.mark.asyncio
async def test_sync_triples_extraction_produces_asserts_edges_with_provenance(
    tmp_path, monkeypatch
):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts

    fact = await facts.add_fact(INSTANCE, "dato", "Ana works at Sezzle", tags=["work"])
    triple_json = (
        '[{"subject": "Ana", "predicate": "works_at", "object": "Sezzle", '
        f'"source": "fact:{fact.id}"}}]'
    )
    fake = _FakeTripleProvider(text=triple_json)
    cfg = _cfg(kg_triples_enabled=True)

    result = await kg.sync(INSTANCE, config=cfg, provider=fake, model="cheap-model")

    assert fake.calls  # the routine model WAS invoked this time
    assert fake.calls[0][0] == "cheap-model"

    asserts = await _asserts_edges(db, INSTANCE)
    assert len(asserts) == 1
    assert asserts[0]["source"] == f"fact:{fact.id}|pred:works_at"

    entities = {e["name"]: e["id"] for e in await db.list_kg_entities(INSTANCE)}
    assert asserts[0]["from_id"] == entities["Ana"]
    assert asserts[0]["to_id"] == entities["Sezzle"]
    assert result["edges"] > 0


@pytest.mark.asyncio
async def test_sync_triples_budget_exceeded_degrades(tmp_path, monkeypatch):
    """A budget/provider failure on the triples pass must not raise, and must
    not stop the regex-derived edges from being written."""
    db = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts

    from lhassa.llm.gateway import BudgetExceeded

    await facts.add_fact(INSTANCE, "dato", "Ana works at Sezzle", tags=["work"])
    fake = _FakeTripleProvider(raises=BudgetExceeded("no budget"))
    cfg = _cfg(kg_triples_enabled=True)

    result = await kg.sync(INSTANCE, config=cfg, provider=fake, model="cheap-model")

    assert result["entities"] >= 2  # Ana, Sezzle, work regex entities still written
    assert result["edges"] > 0  # regex co_occurs/mentions edges still stand
    assert await _asserts_edges(db, INSTANCE) == []  # no asserts edge born


@pytest.mark.asyncio
async def test_sync_triples_ignores_hallucinated_source(tmp_path, monkeypatch):
    """A triple whose 'source' isn't one of the offered ids is dropped, not
    trusted — grounding is enforced, not merely requested."""
    db = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts

    await facts.add_fact(INSTANCE, "dato", "Ana works at Sezzle", tags=["work"])
    triple_json = (
        '[{"subject": "Ana", "predicate": "works_at", "object": "Sezzle", '
        '"source": "fact:not_a_real_id"}]'
    )
    fake = _FakeTripleProvider(text=triple_json)
    cfg = _cfg(kg_triples_enabled=True)

    await kg.sync(INSTANCE, config=cfg, provider=fake, model="cheap-model")

    assert await _asserts_edges(db, INSTANCE) == []


# ── C2: entity resolution ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_merges_exact_normalized_pairs_without_model(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    a = await kg.upsert_entity(INSTANCE, "Sezzle", when="2026-01-01T00:00:00")
    await kg.upsert_entity(INSTANCE, "sezzle", when="2026-01-02T00:00:00")

    cfg = _cfg(kg_triples_enabled=False)
    result = await kg.resolve(INSTANCE, config=cfg)

    assert result == {"merged": 1, "candidates": 1, "skipped": 0}
    remaining = await db.list_kg_entities(INSTANCE)
    assert len(remaining) == 1
    assert remaining[0]["id"] == a  # earlier-seen entity survives


@pytest.mark.asyncio
async def test_resolve_fuzzy_without_model_is_skipped_not_merged(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await kg.upsert_entity(INSTANCE, "Buzz Aldrin", when="2026-01-01T00:00:00")
    await kg.upsert_entity(INSTANCE, "Edwin Aldrin", when="2026-01-01T00:00:00")

    cfg = _cfg(kg_triples_enabled=False)  # no model may run
    result = await kg.resolve(INSTANCE, config=cfg)

    assert result["merged"] == 0
    assert result["candidates"] == 1
    assert result["skipped"] == 1
    assert len(await db.list_kg_entities(INSTANCE)) == 2  # neither guessed away


@pytest.mark.asyncio
async def test_resolve_stubbed_model_merges_aldrin_pair(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    a = await kg.upsert_entity(
        INSTANCE, "Buzz Aldrin", kind=kg.ENTITY_TERM, when="2026-01-01T00:00:00"
    )
    b = await kg.upsert_entity(
        INSTANCE, "Edwin Aldrin", kind=kg.ENTITY_TERM, when="2026-01-02T00:00:00"
    )
    # A third entity co-occurring with the DROPPED entity, so the repoint is
    # actually observable via kg_neighbors after the merge.
    c = await kg.upsert_entity(INSTANCE, "Apollo 11", when="2026-01-02T00:00:00")
    await kg.add_edge(INSTANCE, b, c, kg.CO_OCCURS, when="2026-01-02T00:00:00")

    triple_json = f'[{{"a": "{a}", "b": "{b}", "same": true}}]'
    fake = _FakeTripleProvider(text=triple_json)
    cfg = _cfg(kg_triples_enabled=True)

    result = await kg.resolve(INSTANCE, config=cfg, provider=fake, model="cheap-model")

    assert result["merged"] == 1
    assert result["candidates"] == 1
    assert result["skipped"] == 0

    remaining = {e["name"]: e["id"] for e in await db.list_kg_entities(INSTANCE)}
    assert "Edwin Aldrin" not in remaining
    assert "Buzz Aldrin" in remaining

    # The edge that used to touch the dropped "Edwin Aldrin" now touches the
    # surviving "Buzz Aldrin" entity.
    neighbors = await db.kg_neighbors(INSTANCE, [a])
    assert any(c in (e["from_id"], e["to_id"]) for e in neighbors)


@pytest.mark.asyncio
async def test_resolve_never_raises_on_internal_failure(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    await kg.upsert_entity(INSTANCE, "Sezzle", when="2026-01-01T00:00:00")
    await kg.upsert_entity(INSTANCE, "sezzle", when="2026-01-01T00:00:00")

    async def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(kg.db, "merge_kg_entities", _boom)
    cfg = _cfg()
    result = await kg.resolve(INSTANCE, config=cfg)
    assert result == {"merged": 0, "candidates": 0, "skipped": 0}


# ── C3: retrieval cites its edge (kind + provenance) ─────────────────────────


@pytest.mark.asyncio
async def test_graph_retrieve_via_includes_edge_kind_and_provenance(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, graph_retrieve

    fact1 = await facts.add_fact(INSTANCE, "dato", "Ana works at Sezzle", tags=["work"])
    await facts.add_fact(INSTANCE, "dato", "Sezzle pays through Visa", tags=["finance"])
    cfg = _cfg(memory_layer4_enabled=False)
    await kg.sync(INSTANCE, config=cfg)

    async def _no_semantic(*a, **kw):
        return []

    monkeypatch.setattr("lhassa.memory.load._semantic_layer", _no_semantic)

    # Query "Ana" — a direct entity-name seed — but the second fact (mentioning
    # Visa, not Ana) can only be reached via the 1-hop expansion through Sezzle.
    # The hop's "via" cites the ORIGINATING EDGE's own provenance — the fact
    # that produced the Ana↔Sezzle co_occurs edge (fact1), not the destination
    # fact the hop lands on.
    results = await graph_retrieve.retrieve(INSTANCE, "Ana", config=cfg)
    hop_results = [r for r in results if "—" in r["via"] and "→" in r["via"]]
    assert hop_results
    assert any(f"(fact:{fact1.id})" in r["via"] for r in hop_results)
    assert any("co_occurs" in r["via"] or "mentions" in r["via"] for r in hop_results)
