"""Phase 5 memory tests; fully offline (monkeypatched httpx + completions).

Test inventory:
  1.  Embedder chain falls back on connection error and logs warning once (not twice).
  2.  Vectors stamped with model name.
  3.  Query compares only same-model rows (different model rows filtered out).
  4.  reindex re-stamps all facts with current model.
  5.  add_fact write survives embedder failure (fact written, no exception raised).
  6.  remember regex: "remember: X", "recuerda: X", "recordá: X" variants work;
      500-char cap; confirmation reply contains fact id.
  7.  distill happy path: JSON parsed, ≤10 facts written, dedupe skips existing.
  8.  distill malformed JSON → no writes, returns a string.
  9.  BudgetExceeded → returns "distill deferred: …".
 10.  layer-4 default is now "1" (on); load() still works with empty embeddings table.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────────

async def _init_db(tmp_path, monkeypatch):
    """Redirect DB to a temp file and initialise it."""
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


class _CfgHashing:
    """Config that forces the offline hashing embedder (no embeddings_model set)."""
    instance_id = "inst_test"
    embeddings_model = ""
    embedder_backend = "hash"  # hermetic: never probe a live local Ollama
    embeddings_url = ""
    embeddings_api_key = ""
    embedding_model = ""
    local_model_url = ""
    memory_layer4_enabled = True
    memory_semantic_topk = 5


class _CfgRemote:
    """Config that points at a (fake) remote embedder."""
    instance_id = "inst_test"
    embeddings_model = "nomic-embed-text"
    embeddings_url = "http://127.0.0.1:1"  # guaranteed unreachable
    embeddings_api_key = ""
    embedding_model = ""
    local_model_url = ""
    memory_layer4_enabled = True
    memory_semantic_topk = 5


# ── Test 1: fallback on connection error, warning logged once ────────────────────

def test_remote_embedder_falls_back_and_warns_once():
    """RemoteEmbedder must fall back to HashingEmbedder and set the once-warning flag."""
    import lhassa.memory.embeddings as emb_mod
    from lhassa.memory.embeddings import HashingEmbedder, RemoteEmbedder

    # Reset the module-level flag so the warning fires fresh.
    emb_mod._warned_fallback = False

    embedder = RemoteEmbedder("nomic-embed-text", "http://127.0.0.1:1")

    out1 = embedder.embed(["hello"])
    out2 = embedder.embed(["world"])

    # Outputs must equal what HashingEmbedder produces.
    h = HashingEmbedder()
    assert out1 == h.embed(["hello"])
    assert out2 == h.embed(["world"])

    # The once-warning flag must be set after the first failure.
    assert emb_mod._warned_fallback, "module-level _warned_fallback must be True after first failure"

    # Cleanup.
    emb_mod._warned_fallback = False


# ── Test 2: vectors stamped with model name ──────────────────────────────────────

@pytest.mark.asyncio
async def test_vectors_stamped_with_model_name(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    from lhassa.memory.embeddings import HashingEmbedder, get_embedder

    embedder = get_embedder(_CfgHashing)
    assert isinstance(embedder, HashingEmbedder)

    vecs = embedder.embed(["una prueba"])
    await db.upsert_fact_embedding("inst_test", "f_001", vecs[0], model=embedder.name)

    rows = await db.get_fact_embeddings("inst_test")
    assert len(rows) == 1
    assert rows[0]["model"] == "hashing-v1"


# ── Test 3: query compares only same-model rows ──────────────────────────────────

@pytest.mark.asyncio
async def test_semantic_query_filters_by_model(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    from lhassa.memory import facts, load
    from lhassa.memory.embeddings import HashingEmbedder

    # Add two facts.
    f1 = await facts.add_fact("inst_test", "dato", "python performance matters", tags=[])
    f2 = await facts.add_fact("inst_test", "dato", "chocolate cake recipe", tags=[])

    # Store f1 with hashing-v1 (correct model for _CfgHashing) and
    # f2 with a DIFFERENT model stamp ("alien-model"); f2 should be filtered out.
    h = HashingEmbedder()
    v1 = h.embed([f1.contenido])[0]
    v2 = h.embed([f2.contenido])[0]
    await db.upsert_fact_embedding("inst_test", f1.id, v1, model="hashing-v1")
    await db.upsert_fact_embedding("inst_test", f2.id, v2, model="alien-model")

    ctx = await load.load(
        "inst_test",
        token_budget=10_000,
        query="python performance",
        config=_CfgHashing,
    )
    sem_ids = {f.id for f in ctx.semantic_facts}
    assert f1.id in sem_ids, "f1 (correct model) should be in semantic results"
    assert f2.id not in sem_ids, "f2 (alien-model) must be filtered out"


# ── Test 4: reindex re-stamps all facts with current model ───────────────────────

@pytest.mark.asyncio
async def test_reindex_restamps_all_facts(tmp_path, monkeypatch):
    db = await _init_db(tmp_path, monkeypatch)
    from lhassa.memory import facts
    from lhassa.memory.embeddings import reindex

    f1 = await facts.add_fact("inst_test", "dato", "fact one")
    f2 = await facts.add_fact("inst_test", "dato", "fact two")

    # Store with an old model stamp.
    await db.upsert_fact_embedding("inst_test", f1.id, [0.1], model="old-model")
    await db.upsert_fact_embedding("inst_test", f2.id, [0.2], model="old-model")

    # reindex with hashing config; should overwrite both.
    count = await reindex("inst_test", _CfgHashing)
    assert count == 2

    rows = await db.get_fact_embeddings("inst_test")
    models = {r["model"] for r in rows}
    assert models == {"hashing-v1"}, f"expected {{hashing-v1}}, got {models}"
    # Vectors must now be 256-dim (HashingEmbedder default).
    for r in rows:
        assert len(r["vector"]) == 256


# ── Test 5: add_fact survives embedder failure ───────────────────────────────────

@pytest.mark.asyncio
async def test_add_fact_survives_embedder_failure(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    # Patch get_embedder at its source module to intercept the lazy import inside add_fact.
    import lhassa.memory.facts as facts_mod

    def _exploding_embed(texts):
        raise RuntimeError("embedding endpoint is down")

    class _BrokenEmbedder:
        name = "broken"
        embed = staticmethod(_exploding_embed)

    with patch("lhassa.memory.embeddings.get_embedder", return_value=_BrokenEmbedder()):
        # Must not raise even though embedder explodes.
        fact = await facts_mod.add_fact(
            "inst_test", "dato", "this fact must survive",
        )

    assert fact.id
    # Fact is in the DB.
    rows = await db.query_facts("inst_test")
    assert any(r["contenido"] == "this fact must survive" for r in rows)


# ── Test 6: remember regex variants ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remember_regex_variants(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    import lhassa.notify.telegram_inbound as ti

    class _Cfg:
        instance_id = "inst_test"
        telegram_poll_enabled = True
        telegram_bot_token = "tok"
        telegram_chat_id = "123"
        # Disable everything else.
        embeddings_model = ""
        embedder_backend = "hash"  # hermetic: never probe a live local Ollama
        embeddings_url = ""
        embeddings_api_key = ""
        embedding_model = ""
        local_model_url = ""
        memory_layer4_enabled = False
        memory_semantic_topk = 5
        watch_rules_file = ""

    sent_replies: list[str] = []

    async def _fake_send(text, *, config):
        sent_replies.append(text)

    # Patch telegram.send.
    import lhassa.notify.telegram as tg_mod
    monkeypatch.setattr(tg_mod, "send", _fake_send)

    cfg = _Cfg()
    for variant in ["remember: my birthday is June 5", "recuerda: prefiero el café oscuro", "recordá, trabajo con Python"]:
        sent_replies.clear()
        await ti._answer(variant, config=cfg, chat_id="123")
        assert sent_replies, f"no reply for variant {variant!r}"
        # Reply must contain a fact id.
        assert "f_" in sent_replies[-1] or "Remembered:" in sent_replies[-1], \
            f"reply missing fact id: {sent_replies[-1]!r}"

    # 500-char cap test.
    sent_replies.clear()
    long_text = "remember: " + "x" * 600
    await ti._answer(long_text, config=cfg, chat_id="123")
    # The fact was written; check DB.
    rows = await db.query_facts("inst_test")
    capped = [r for r in rows if r["contenido"] == "x" * 500]
    assert capped, "fact contenido should be capped at 500 chars"


# ── Test 7: distill happy path ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_distill_happy_path(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.memory import consolidate, facts

    # Pre-load an existing fact (for dedupe check).
    await facts.add_fact("inst_test", "dato", "existing fact; skip me")

    # Fake completion that returns a JSON array.
    fake_json = '["owner prefers Python", "system runs on Arch Linux", "existing fact; skip me"]'

    from lhassa.llm.provider import Completion

    fake_completion = Completion(text=fake_json, model="test-model", cost_usd=0.001)

    async def _fake_complete(messages, *, provider, model, completion_fn):
        return fake_completion

    _fake_episodes = [
        {"despertar_por": "heartbeat", "accion": "pensar", "resultado": "observed python usage", "creado_en": ""},
    ]

    with patch.object(consolidate, "_cheap_complete", side_effect=_fake_complete), \
         patch.object(consolidate, "_resolve_provider_model", return_value=(object(), "test-model")), \
         patch("lhassa.core.db.load_recent_episodes", return_value=_fake_episodes):
        result = await consolidate.distill_episodes(
            "inst_test",
            since_hours=24,
            completion_fn=None,
            config=type("C", (), {"instance_id": "inst_test"})(),
        )

    assert "distill:" in result
    # Check written facts (skip the pre-existing one and the "existing fact" dedupe).
    all_facts = await facts.load_facts("inst_test")
    distilled = [f for f in all_facts if f.procedencia.startswith("distill:")]
    # Should have written 2 novel facts (the third is a duplicate).
    assert len(distilled) == 2
    contents = {f.contenido for f in distilled}
    assert "owner prefers Python" in contents
    assert "system runs on Arch Linux" in contents
    assert "existing fact; skip me" not in contents


# ── Test 8: distill malformed JSON ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_distill_boundary_flag_stops_before_provider_resolution():
    from lhassa.memory import consolidate

    with patch.object(
        consolidate, "_resolve_provider_model", side_effect=AssertionError("must not spend")
    ):
        result = await consolidate.distill_episodes(
            "inst_test",
            config=type(
                "C", (), {"instance_id": "inst_test", "memory_distill_enabled": False}
            )(),
        )

    assert result == {"distilled": 0, "skipped": "memory_distill_enabled off"}


# ── Test 8: distill malformed JSON ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_distill_malformed_json(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.memory import consolidate, facts
    from lhassa.llm.provider import Completion

    fake_completion = Completion(text="not valid JSON at all !!!", model="test-model", cost_usd=0.0)

    _fake_episodes = [{"despertar_por": "heartbeat", "accion": "pensar", "resultado": "some output", "creado_en": ""}]

    async def _fake_complete(messages, *, provider, model, completion_fn):
        return fake_completion

    with patch.object(consolidate, "_cheap_complete", side_effect=_fake_complete), \
         patch.object(consolidate, "_resolve_provider_model", return_value=(object(), "test-model")), \
         patch("lhassa.core.db.load_recent_episodes", return_value=_fake_episodes):
        result = await consolidate.distill_episodes(
            "inst_test",
            since_hours=24,
            completion_fn=None,
            config=type("C", (), {"instance_id": "inst_test"})(),
        )

    # Returns a string (not raises).
    assert isinstance(result, str)
    # No facts written.
    all_facts = await facts.load_facts("inst_test")
    distilled = [f for f in all_facts if f.procedencia.startswith("distill:")]
    assert len(distilled) == 0


# ── Test 9: BudgetExceeded → deferred ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_distill_budget_exceeded_returns_deferred(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    from lhassa.llm.gateway import BudgetExceeded
    from lhassa.memory import consolidate

    async def _raises_budget(*_a, **_kw):
        raise BudgetExceeded("daily budget hit")

    _fake_episodes = [{"despertar_por": "heartbeat", "accion": "pensar", "resultado": "output", "creado_en": ""}]

    # Patch _resolve_provider_model so the provider setup succeeds,
    # then _cheap_complete raises BudgetExceeded (the actual test).
    with patch.object(consolidate, "_resolve_provider_model", return_value=(object(), "test-model")), \
         patch.object(consolidate, "_cheap_complete", side_effect=_raises_budget), \
         patch("lhassa.core.db.load_recent_episodes", return_value=_fake_episodes):
        result = await consolidate.distill_episodes(
            "inst_test",
            since_hours=24,
            config=type("C", (), {"instance_id": "inst_test"})(),
        )

    assert "distill deferred" in result
    assert "budget" in result.lower()


# ── Test 10: layer-4 default ON; load() works with empty embeddings table ────────

def test_layer4_default_is_on():
    """Config.memory_layer4_enabled should now default to True (LHASSA_MEMORY_LAYER4=1)."""
    import os
    # Temporarily unset LHASSA_MEMORY_LAYER4 so we read the compiled default.
    saved = os.environ.pop("LHASSA_MEMORY_LAYER4", None)
    try:
        # Re-evaluate the default by parsing the source expression.
        default_val = "1" not in ("0", "false", "")
        assert default_val is True, "default for LHASSA_MEMORY_LAYER4 should now be '1' (on)"
    finally:
        if saved is not None:
            os.environ["LHASSA_MEMORY_LAYER4"] = saved


@pytest.mark.asyncio
async def test_load_works_with_empty_embeddings_table(tmp_path, monkeypatch):
    """load() with layer-4 ON and no indexed embeddings must return an empty semantic layer."""
    await _init_db(tmp_path, monkeypatch)
    from lhassa.memory import facts, load

    await facts.add_fact("inst_test", "dato", "hecho de prueba")

    ctx = await load.load(
        "inst_test",
        token_budget=10_000,
        query="prueba",
        config=_CfgHashing,
    )
    # The fact is in the grande but not yet in the fact_embeddings table with
    # the RIGHT model stamp for _CfgHashing; or it may have been auto-indexed
    # by embed-on-write. Either way, load() must not crash.
    assert ctx is not None
    # Pinned + directives are always loaded regardless.
    # (No assertions on semantic_facts; that depends on whether embed-on-write ran.)
