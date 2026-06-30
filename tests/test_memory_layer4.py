"""Tests for memory layer 4 (semantic retrieval) wired into load() + db storage."""

import pytest


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


class _Cfg:
    """A minimal config: layer 4 ON, offline hashing embedder."""
    instance_id = "inst_personal"
    memory_layer4_enabled = True
    embedding_model = ""
    local_model_url = ""
    memory_semantic_topk = 5


class _CfgOff(_Cfg):
    memory_layer4_enabled = False


# ── db storage helpers ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fact_embeddings_table_exists(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute("PRAGMA table_info(fact_embeddings)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert {"fact_id", "instance_id", "dim", "vector", "model", "creado_en"} <= cols


@pytest.mark.asyncio
async def test_upsert_and_get_fact_embedding_roundtrip(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await db.upsert_fact_embedding("inst_personal", "f_1", [0.1, 0.2, 0.3], model="hashing")
    rows = await db.get_fact_embeddings("inst_personal")
    assert len(rows) == 1
    assert rows[0]["fact_id"] == "f_1"
    assert rows[0]["vector"] == [0.1, 0.2, 0.3]
    assert rows[0]["model"] == "hashing"


@pytest.mark.asyncio
async def test_upsert_fact_embedding_overwrites(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await db.upsert_fact_embedding("inst_personal", "f_1", [1.0, 0.0])
    await db.upsert_fact_embedding("inst_personal", "f_1", [0.0, 1.0])
    rows = await db.get_fact_embeddings("inst_personal")
    assert len(rows) == 1
    assert rows[0]["vector"] == [0.0, 1.0]


@pytest.mark.asyncio
async def test_fact_embeddings_isolated_by_instance(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await db.upsert_fact_embedding("inst_a", "f_a", [1.0])
    await db.upsert_fact_embedding("inst_b", "f_b", [2.0])
    rows = await db.get_fact_embeddings("inst_a")
    assert [r["fact_id"] for r in rows] == ["f_a"]


# ── index_facts ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_index_facts_is_idempotent(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    await facts.add_fact("inst_personal", "dato", "hecho uno")
    await facts.add_fact("inst_personal", "dato", "hecho dos")
    n1 = await load.index_facts("inst_personal", _Cfg)
    assert n1 == 2
    n2 = await load.index_facts("inst_personal", _Cfg)
    assert n2 == 0  # nothing re-embedded


# ── load() layer 4: DoD ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_layer4_surfaces_related_fact_when_enabled(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    # A semantically related fact that shares NO tags and is NOT pinned.
    await facts.add_fact(
        "inst_personal", "dato",
        "para optimizar el rendimiento conviene reducir la latencia",
        tags=["perf"],
    )
    await facts.add_fact(
        "inst_personal", "dato", "receta de pastel de chocolate", tags=["cocina"],
    )
    await load.index_facts("inst_personal", _Cfg)

    ctx = await load.load(
        "inst_personal",
        token_budget=10_000,
        active_tags=(),  # NOT selected by tags
        query="cómo mejorar el rendimiento y bajar la latencia del sistema",
        config=_Cfg,
    )
    contents = {f.contenido for f in ctx.semantic_facts}
    assert "para optimizar el rendimiento conviene reducir la latencia" in contents


@pytest.mark.asyncio
async def test_layer4_noop_when_disabled(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    await facts.add_fact("inst_personal", "dato", "hecho de optimización", tags=["perf"])
    await load.index_facts("inst_personal", _Cfg)

    ctx = await load.load(
        "inst_personal",
        token_budget=10_000,
        query="optimización",
        config=_CfgOff,
    )
    assert ctx.semantic_facts == []  # disabled → default behavior unchanged


@pytest.mark.asyncio
async def test_layer4_noop_without_query(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    await facts.add_fact("inst_personal", "dato", "hecho de optimización", tags=["perf"])
    await load.index_facts("inst_personal", _Cfg)

    ctx = await load.load("inst_personal", token_budget=10_000, config=_Cfg)
    assert ctx.semantic_facts == []


@pytest.mark.asyncio
async def test_layer4_noop_without_index(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    await facts.add_fact("inst_personal", "dato", "hecho de optimización", tags=["perf"])
    # No index built.
    ctx = await load.load(
        "inst_personal", token_budget=10_000, query="optimización", config=_Cfg,
    )
    assert ctx.semantic_facts == []


@pytest.mark.asyncio
async def test_layer4_excludes_already_selected(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    await facts.add_fact(
        "inst_personal", "dato", "optimizar el rendimiento del sistema", tags=["perf"],
    )
    await load.index_facts("inst_personal", _Cfg)
    ctx = await load.load(
        "inst_personal",
        token_budget=10_000,
        active_tags=["perf"],  # the fact is selected by tags here
        query="optimizar rendimiento",
        config=_Cfg,
    )
    tag_contents = {f.contenido for f in ctx.tag_facts}
    sem_contents = {f.contenido for f in ctx.semantic_facts}
    assert "optimizar el rendimiento del sistema" in tag_contents
    assert sem_contents.isdisjoint(tag_contents)  # never double-counted


# ── truncate drops semantic first ────────────────────────────────────────────────

def test_truncate_drops_semantic_before_tags_and_recent():
    from lhassa.memory.facts import Fact
    from lhassa.memory.load import Context, _render_episode, _render_fact, estimate_tokens, truncate

    pinned = Fact(id="p", instance_id="i", tipo="dato", contenido="nucleo", pinned=True)
    tagf = Fact(id="t", instance_id="i", tipo="dato", contenido="tag fact aqui")
    semf = Fact(id="s", instance_id="i", tipo="dato", contenido="semantic fact aqui")
    ep = {"creado_en": "2026", "despertar_por": "hb", "resultado": "ok"}

    ctx = Context(
        instance_id="i",
        directives=["dir uno"],
        pinned_facts=[pinned],
        tag_facts=[tagf],
        episodes=[ep],
        semantic_facts=[semf],
    )
    mandatory = (
        estimate_tokens("Directivas (siempre vigentes):\n- dir uno")
        + estimate_tokens("Hechos fijados:\n" + _render_fact(pinned))
    )
    ep_tok = estimate_tokens(_render_episode(ep))
    tag_tok = estimate_tokens(_render_fact(tagf))
    # Budget fits identity + recent + tags, but NOT the semantic fact.
    budget = mandatory + ep_tok + tag_tok
    out = truncate(ctx, budget)
    assert out.semantic_facts == []          # semantic dropped first
    assert len(out.tag_facts) == 1           # tags kept
    assert len(out.episodes) == 1            # recent kept
    assert any(f.contenido == "nucleo" for f in out.pinned_facts)  # core kept


def test_truncate_drops_semantic_then_tags():
    from lhassa.memory.facts import Fact
    from lhassa.memory.load import Context, _render_episode, estimate_tokens, truncate

    tagf = Fact(id="t", instance_id="i", tipo="dato", contenido="tag fact aqui")
    semf = Fact(id="s", instance_id="i", tipo="dato", contenido="semantic fact aqui")
    ep = {"creado_en": "2026", "despertar_por": "hb", "resultado": "ok"}

    ctx = Context(
        instance_id="i",
        directives=["dir uno"],
        tag_facts=[tagf],
        episodes=[ep],
        semantic_facts=[semf],
    )
    mandatory = estimate_tokens("Directivas (siempre vigentes):\n- dir uno")
    ep_tok = estimate_tokens(_render_episode(ep))
    # Budget fits identity + recent only — both semantic and tags must go.
    budget = mandatory + ep_tok
    out = truncate(ctx, budget)
    assert out.semantic_facts == []
    assert out.tag_facts == []
    assert len(out.episodes) == 1  # recent outranks both
