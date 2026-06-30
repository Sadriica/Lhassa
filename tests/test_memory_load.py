"""Tests for lhassa.memory.load and lhassa.memory.seed — the layered chica."""

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
    # The directiva facts match the persona directives — must appear once each.
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
