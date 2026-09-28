"""Tests for lhassa.memory.load and lhassa.memory.seed; the layered chica."""

import pytest


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


async def _make_episode(db, instance_id, despertar_por):
    from lhassa.core.episode import Episode
    ep = Episode.begin(despertar_por=despertar_por, instance_id=instance_id)
    ep.finish(decision="d", accion="a", resultado="r")
    await db.append_episode(ep)


# ── seeding ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_default_facts_idempotent(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, seed
    n1 = await seed.seed_default_facts("inst_personal")
    assert n1 == 4
    n2 = await seed.seed_default_facts("inst_personal")
    assert n2 == 0  # nothing duplicated
    rows = await facts.load_facts("inst_personal")
    assert len(rows) == 4
    pinned = {f.contenido for f in rows if f.pinned}
    assert "prefiere herramientas de código libre" in pinned
    assert "responder conciso por defecto" in pinned


# ── load() ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_always_includes_directivas_and_pinned(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import load, seed
    await seed.seed_default_facts("inst_personal")
    ctx = await load.load("inst_personal", token_budget=10_000)

    # Persona directives are always present (folded as identity).
    assert "responder conciso por defecto" in ctx.directives
    assert "optimizar; muy sensible a cualquier delay" in ctx.directives
    # The pinned dato survives in the pinned facts.
    pinned_contents = {f.contenido for f in ctx.pinned_facts}
    assert "prefiere herramientas de código libre" in pinned_contents
    # The non-pinned dato is NOT auto-loaded (no tags requested).
    assert "le gusta el chocolate con menta" not in pinned_contents


@pytest.mark.asyncio
async def test_load_directivas_not_duplicated(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import load, seed
    await seed.seed_default_facts("inst_personal")
    ctx = await load.load("inst_personal", token_budget=10_000)
    # The directiva facts match the persona directives; must appear once each.
    assert ctx.directives.count("responder conciso por defecto") == 1
    assert ctx.directives.count("optimizar; muy sensible a cualquier delay") == 1


@pytest.mark.asyncio
async def test_load_active_tags_pull_context(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    await facts.add_fact("inst_personal", "dato", "trabaja en proyecto X", tags=["proj_x"])
    await facts.add_fact("inst_personal", "dato", "otra cosa", tags=["proj_y"])

    ctx = await load.load("inst_personal", token_budget=10_000, active_tags=["proj_x"])
    tag_contents = {f.contenido for f in ctx.tag_facts}
    assert "trabaja en proyecto X" in tag_contents
    assert "otra cosa" not in tag_contents


@pytest.mark.asyncio
async def test_load_includes_recent_episodes(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.memory import load
    await _make_episode(db, "inst_personal", "heartbeat_1")
    await _make_episode(db, "inst_personal", "heartbeat_2")
    ctx = await load.load("inst_personal", token_budget=10_000)
    assert len(ctx.episodes) == 2


@pytest.mark.asyncio
async def test_load_isolated_by_instance(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    await facts.add_fact("inst_a", "dato", "secret_a", pinned=True)
    await facts.add_fact("inst_b", "dato", "secret_b", pinned=True)
    await _make_episode(db, "inst_a", "from_a")

    ctx = await load.load("inst_a", token_budget=10_000)
    contents = {f.contenido for f in ctx.facts}
    assert "secret_a" in contents
    assert "secret_b" not in contents
    assert all(e["instance_id"] == "inst_a" for e in ctx.episodes)


# ── token budget / truncation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_respects_budget_drops_tags_before_recent(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load

    await facts.add_fact("inst_personal", "directiva", "responder conciso", pinned=True)
    await facts.add_fact("inst_personal", "dato", "tag fact uno", tags=["active"])
    await _make_episode(db, "inst_personal", "ep_uno")

    # Budget large enough for identity + recent, but not the tag layer.
    full = await load.load("inst_personal", token_budget=100_000, active_tags=["active"])
    assert full.tag_facts and full.episodes  # everything fits when budget is huge

    # Build a budget that fits the identity core + the recent episode but leaves
    # no room for the tag fact, using truncate's own per-item accounting.
    from lhassa.memory.load import estimate_tokens, _render_fact, _render_episode
    dir_block = "Directivas (siempre vigentes):\n" + "\n".join(
        f"- {d}" for d in full.directives
    )
    mandatory = estimate_tokens(dir_block)  # no pinned datos here
    ep_tok = estimate_tokens(_render_episode(full.episodes[0]))
    tag_tok = estimate_tokens(_render_fact(full.tag_facts[0]))
    budget = mandatory + ep_tok  # fits identity + recent, not the tag fact

    ctx = await load.load("inst_personal", token_budget=budget, active_tags=["active"])
    assert ctx.tag_facts == []          # tags dropped first
    assert len(ctx.episodes) == 1       # recent kept (higher priority)
    assert "responder conciso" in ctx.directives  # identity never dropped
    assert tag_tok > 0


@pytest.mark.asyncio
async def test_load_keeps_identity_even_over_budget(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    await facts.add_fact("inst_personal", "dato", "pinned dato", pinned=True)
    ctx = await load.load("inst_personal", token_budget=1)
    # Identity core survives even though it exceeds the tiny budget.
    assert any(f.contenido == "pinned dato" for f in ctx.pinned_facts)
    assert ctx.directives  # persona directives still present


@pytest.mark.asyncio
async def test_context_to_prompt_and_estimate(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    await facts.add_fact("inst_personal", "dato", "un dato fijado", pinned=True)
    ctx = await load.load("inst_personal", token_budget=10_000)
    prompt = ctx.to_prompt()
    assert "Directivas" in prompt
    assert "un dato fijado" in prompt
    assert ctx.token_estimate > 0


# ── knowledge layer (document perception, lowest priority) ───────────────────


@pytest.mark.asyncio
async def test_load_knowledge_layer_adds_chunk_when_query_matches(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from types import SimpleNamespace

    from lhassa.memory import knowledge, load

    root = tmp_path / "notes"
    root.mkdir()
    (root / "trip.md").write_text("The African elephant sanctuary visit was unforgettable.")

    cfg = SimpleNamespace(
        instance_id="inst_personal",
        knowledge_roots=str(root),
        knowledge_max_file_kb=512,
        knowledge_chunk_chars=2000,
        memory_layer4_enabled=False,
    )
    await knowledge.ingest("inst_personal", config=cfg)

    ctx = await load.load(
        "inst_personal", token_budget=10_000, query="elephant sanctuary", config=cfg
    )
    assert ctx.knowledge_chunks
    prompt = ctx.to_prompt()
    assert "from your notes: trip.md" in prompt
    assert "elephant" in prompt.lower()


@pytest.mark.asyncio
async def test_load_knowledge_layer_empty_when_query_does_not_match(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from types import SimpleNamespace

    from lhassa.memory import knowledge, load

    root = tmp_path / "notes"
    root.mkdir()
    (root / "trip.md").write_text("The African elephant sanctuary visit was unforgettable.")

    cfg = SimpleNamespace(
        instance_id="inst_personal",
        knowledge_roots=str(root),
        knowledge_max_file_kb=512,
        knowledge_chunk_chars=2000,
        memory_layer4_enabled=False,
    )
    await knowledge.ingest("inst_personal", config=cfg)

    ctx = await load.load(
        "inst_personal", token_budget=10_000, query=None, config=cfg
    )
    assert ctx.knowledge_chunks == []


@pytest.mark.asyncio
async def test_load_knowledge_layer_defensive_on_failure(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import knowledge, load as load_mod

    async def _boom(*a, **kw):
        raise RuntimeError("knowledge exploded")

    monkeypatch.setattr(knowledge, "search", _boom)
    # _knowledge_layer wraps knowledge.search in its own try/except (mirrors
    # _recall_layer exactly); a broken search must degrade to an empty layer,
    # never raise up through load().
    result = await load_mod._knowledge_layer("inst_personal", "x", None)
    assert result == []

    ctx = await load_mod.load("inst_personal", token_budget=10_000, query="x")
    assert ctx.knowledge_chunks == []


# ── estimate_tokens (tiktoken optional, deterministic, never raises) ──────────


def test_estimate_tokens_heuristic_without_tiktoken(monkeypatch):
    from lhassa.memory import load as load_mod

    monkeypatch.setattr(load_mod, "_get_tiktoken_encoding", lambda: None)
    assert load_mod.estimate_tokens("") == 0
    # ~3 chars/token safety margin (denser than the old 4 chars/token).
    assert load_mod.estimate_tokens("abcdef") == (6 + 2) // 3      # 2
    assert load_mod.estimate_tokens("a" * 9) == 3


def test_estimate_tokens_uses_tiktoken_when_available(monkeypatch):
    from lhassa.memory import load as load_mod

    class FakeEnc:
        def encode(self, text):
            return list(text.split())  # one "token" per whitespace word

    monkeypatch.setattr(load_mod, "_get_tiktoken_encoding", lambda: FakeEnc())
    assert load_mod.estimate_tokens("one two three") == 3
    assert load_mod.estimate_tokens("") == 0  # empty short-circuits before encode


def test_estimate_tokens_degrades_on_tiktoken_import_failure(monkeypatch):
    import sys

    from lhassa.memory import load as load_mod

    # Force a fresh probe whose `import tiktoken` fails (sys.modules[...] = None).
    monkeypatch.setattr(load_mod, "_TIKTOKEN_TRIED", False)
    monkeypatch.setattr(load_mod, "_TIKTOKEN_ENC", None)
    monkeypatch.setitem(sys.modules, "tiktoken", None)

    assert load_mod._get_tiktoken_encoding() is None
    # Falls back to the char heuristic, deterministically, without raising.
    assert load_mod.estimate_tokens("abcdef") == 2


# ── graph layer (Wave 3, LightRAG-lite; lowest priority) ────────────────────


async def _sync_kg_fixture(config):
    """Two facts sharing 'Sezzle' so a 1-hop expansion from 'Ana' surfaces a
    SECOND fact (not the direct entity-match seed); exercises the
    "connected: X —kind→ Y" citation, not just a direct name match."""
    from lhassa.memory import facts, kg

    await facts.add_fact("inst_personal", "dato", "Ana works at Sezzle", tags=["work"])
    await facts.add_fact("inst_personal", "dato", "Sezzle pays through Visa", tags=["finance"])
    return await kg.sync("inst_personal", config=config)


@pytest.mark.asyncio
async def test_load_graph_layer_absent_when_flag_off(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from types import SimpleNamespace

    from lhassa.memory import load

    cfg = SimpleNamespace(
        instance_id="inst_personal",
        memory_graph_enabled=True,  # ON for the sync fixture ...
        memory_layer4_enabled=False,
    )
    await _sync_kg_fixture(cfg)

    # ... but OFF for the load() call; the default, and the flag load() itself
    # checks. The layer must not fire even though the graph is populated.
    off_cfg = SimpleNamespace(
        instance_id="inst_personal",
        memory_graph_enabled=False,
        memory_layer4_enabled=False,
    )
    ctx = await load.load("inst_personal", token_budget=10_000, query="Ana", config=off_cfg)
    assert ctx.graph_items == []
    assert "connected:" not in ctx.to_prompt()


@pytest.mark.asyncio
async def test_load_graph_layer_adds_hop_derived_fact_when_flag_on(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from types import SimpleNamespace

    from lhassa.memory import load

    cfg = SimpleNamespace(
        instance_id="inst_personal",
        memory_graph_enabled=True,
        memory_layer4_enabled=False,
    )
    await _sync_kg_fixture(cfg)

    # Isolate the KG-hop path from the always-on fact-embedding side effect
    # (facts.add_fact embeds on write regardless of memory_layer4_enabled);
    # this test targets graph_retrieve's OWN 1-hop expansion, not whichever
    # embedder backend happens to be reachable in this environment.
    async def _no_semantic(*a, **kw):
        return []

    monkeypatch.setattr(load, "_semantic_layer", _no_semantic)

    ctx = await load.load("inst_personal", token_budget=10_000, query="Ana", config=cfg)
    assert ctx.graph_items
    prompt = ctx.to_prompt()
    assert "connected:" in prompt
    # The hop-derived fact (reached via Sezzle, NOT the direct "Ana" match) is
    # what proves 1-hop expansion actually ran, not just a name match.
    assert "Visa" in prompt
    assert any("—co_occurs→" in item["via"] or "—mentions→" in item["via"] for item in ctx.graph_items)


@pytest.mark.asyncio
async def test_load_graph_layer_empty_when_no_query(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from types import SimpleNamespace

    from lhassa.memory import load

    cfg = SimpleNamespace(
        instance_id="inst_personal",
        memory_graph_enabled=True,
        memory_layer4_enabled=False,
    )
    await _sync_kg_fixture(cfg)

    ctx = await load.load("inst_personal", token_budget=10_000, query=None, config=cfg)
    assert ctx.graph_items == []


@pytest.mark.asyncio
async def test_load_graph_layer_defensive_on_failure(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import graph_retrieve, load as load_mod

    async def _boom(*a, **kw):
        raise RuntimeError("graph exploded")

    monkeypatch.setattr(graph_retrieve, "retrieve", _boom)
    result = await load_mod._graph_layer("inst_personal", "x", None)
    assert result == []

    from types import SimpleNamespace

    cfg = SimpleNamespace(memory_graph_enabled=True, memory_layer4_enabled=False)
    ctx = await load_mod.load("inst_personal", token_budget=10_000, query="x", config=cfg)
    assert ctx.graph_items == []


def test_truncate_drops_graph_before_knowledge():
    """Graph is the LOWEST-priority layer; under a tight budget it must be
    dropped before the (still low-priority, but ranked above it) knowledge
    layer."""
    from lhassa.memory import load as load_mod

    knowledge_chunk = {"source_path": "notes/trip.md", "text": "elephant sanctuary notes"}
    graph_item = {"text": "Sezzle pays through Visa", "via": "Ana —co_occurs→ Sezzle", "source": "fact:x"}

    ctx = load_mod.Context(instance_id="inst_personal")
    ctx.knowledge_chunks = [knowledge_chunk]
    ctx.graph_items = [graph_item]

    knowledge_tok = load_mod.estimate_tokens(load_mod._render_knowledge(knowledge_chunk))
    graph_tok = load_mod.estimate_tokens(load_mod._render_graph(graph_item))
    budget = knowledge_tok + graph_tok - 1  # not enough room for both

    load_mod.truncate(ctx, budget)

    assert ctx.knowledge_chunks == [knowledge_chunk]
    assert ctx.graph_items == []
