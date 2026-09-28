"""
Tests for lhassa.memory.chica; the persistent working-memory cache.

Phase 1 is mechanical: LRU consolidation by threshold (NO tokens, NO LLM, NO
semantics) + regeneration from the grande. Offline; tmp paths via monkeypatch.
"""

import pytest


async def _init(tmp_path, monkeypatch):
    """Reroute both the grande (db) and the chica to tmp files; init the grande."""
    import lhassa.core.db as db
    import lhassa.memory.chica as chica
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    monkeypatch.setattr(chica, "CHICA_DB_PATH", tmp_path / "test_chica.db")
    await db.init_db()
    return db, chica


# ── remember / touch / LRU order ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remember_and_items_mru_first(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    await chica.remember("inst_personal", "a", "alpha")
    await chica.remember("inst_personal", "b", "beta")
    await chica.remember("inst_personal", "c", "gamma")

    keys = [it["key"] for it in await chica.items("inst_personal")]
    assert keys == ["c", "b", "a"]  # most-recently-used first
    assert await chica.size("inst_personal") == 3


@pytest.mark.asyncio
async def test_touch_bumps_lru_order(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    await chica.remember("inst_personal", "a", "alpha")
    await chica.remember("inst_personal", "b", "beta")
    await chica.remember("inst_personal", "c", "gamma")

    assert await chica.touch("inst_personal", "a") is True
    keys = [it["key"] for it in await chica.items("inst_personal")]
    assert keys == ["a", "c", "b"]  # a is now most-recently-used


@pytest.mark.asyncio
async def test_touch_absent_key_is_noop(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    assert await chica.touch("inst_personal", "missing") is False


@pytest.mark.asyncio
async def test_remember_existing_key_upserts_and_bumps(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    await chica.remember("inst_personal", "a", "alpha")
    await chica.remember("inst_personal", "b", "beta")
    await chica.remember("inst_personal", "a", "alpha-2")  # update + bump

    items = await chica.items("inst_personal")
    assert await chica.size("inst_personal") == 2  # no duplicate row
    assert items[0]["key"] == "a"
    assert items[0]["content"] == "alpha-2"


# ── consolidate: threshold-triggered LRU eviction ────────────────────────────────

@pytest.mark.asyncio
async def test_consolidate_below_threshold_evicts_nothing(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    for i in range(5):
        await chica.remember("inst_personal", f"k{i}", f"v{i}")
    res = await chica.consolidate("inst_personal", max_items=10)
    assert res == {"kept": 5, "evicted": 0}
    assert await chica.size("inst_personal") == 5


@pytest.mark.asyncio
async def test_consolidate_evicts_least_recently_used(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    for i in range(10):
        await chica.remember("inst_personal", f"k{i}", f"v{i}")
    # Touch k0 and k1 so they are the most-recently-used and survive.
    await chica.touch("inst_personal", "k0")
    await chica.touch("inst_personal", "k1")

    res = await chica.consolidate("inst_personal", max_items=5)
    assert res == {"kept": 5, "evicted": 5}
    assert await chica.size("inst_personal") == 5

    keys = {it["key"] for it in await chica.items("inst_personal")}
    assert "k0" in keys and "k1" in keys      # recently used survived
    assert "k2" not in keys and "k3" not in keys  # LRU evicted


@pytest.mark.asyncio
async def test_consolidate_never_evicts_pinned_or_directiva(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    # Three protected entries (older = lowest seq, so first in line to evict).
    await chica.remember("inst_personal", "p0", "pinned", pinned=True)
    await chica.remember("inst_personal", "d0", "directiva", kind=chica.KIND_DIRECTIVA)
    await chica.remember("inst_personal", "p1", "pinned2", pinned=True)
    # Then a pile of evictable items.
    for i in range(7):
        await chica.remember("inst_personal", f"k{i}", f"v{i}")

    res = await chica.consolidate("inst_personal", max_items=4)
    assert res["evicted"] == 6  # 10 total - 4 threshold
    keys = {it["key"] for it in await chica.items("inst_personal")}
    assert {"p0", "p1", "d0"}.issubset(keys)  # protected never dropped
    assert await chica.size("inst_personal") == 4


@pytest.mark.asyncio
async def test_consolidate_protected_exceed_threshold_kept_above(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    for i in range(5):
        await chica.remember("inst_personal", f"p{i}", f"v{i}", pinned=True)
    await chica.remember("inst_personal", "e0", "evictable")

    res = await chica.consolidate("inst_personal", max_items=2)
    # Only the single evictable item can go; pinned stay above the threshold.
    assert res == {"kept": 5, "evicted": 1}


# ── clear: kill the chica ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_empties_instance(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    await chica.remember("inst_personal", "a", "alpha", pinned=True)
    await chica.remember("inst_personal", "b", "beta")
    removed = await chica.clear("inst_personal")
    assert removed == 2
    assert await chica.size("inst_personal") == 0


# ── regenerate: rebuild from the grande ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_regenerate_rebuilds_pinned_and_directivas(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    from lhassa.memory import seed
    await seed.seed_default_facts("inst_personal")

    # Prime the chica, then kill it.
    n_first = await chica.regenerate("inst_personal", token_budget=10_000)
    assert n_first > 0
    await chica.clear("inst_personal")
    assert await chica.size("inst_personal") == 0

    # Regenerate from the grande; identity is back.
    n = await chica.regenerate("inst_personal", token_budget=10_000)
    assert n > 0
    items = await chica.items("inst_personal")
    contents = {it["content"] for it in items}
    # Directivas present and pinned (identity, never evicted).
    assert "responder conciso por defecto" in contents
    assert "optimizar; muy sensible a cualquier delay" in contents
    directivas = [it for it in items if it["kind"] == chica.KIND_DIRECTIVA]
    assert directivas and all(it["pinned"] for it in directivas)
    # Pinned dato present.
    assert "prefiere herramientas de código libre" in contents


@pytest.mark.asyncio
async def test_regenerate_includes_active_tag_facts(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_personal", "dato", "trabaja en proyecto X", tags=["proj_x"])

    n = await chica.regenerate(
        "inst_personal", token_budget=10_000, active_tags=["proj_x"]
    )
    assert n >= 1
    contents = {it["content"] for it in await chica.items("inst_personal")}
    assert "trabaja en proyecto X" in contents


@pytest.mark.asyncio
async def test_regenerate_survives_consolidate_keeping_identity(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts, seed
    await seed.seed_default_facts("inst_personal")
    # Add many tag facts so the chica is fat, then regenerate them in.
    for i in range(20):
        await facts.add_fact("inst_personal", "dato", f"nota {i}", tags=["bulk"])

    await chica.regenerate("inst_personal", token_budget=100_000, active_tags=["bulk"])
    before = await chica.size("inst_personal")
    assert before > 5

    res = await chica.consolidate("inst_personal", max_items=5)
    assert res["evicted"] > 0
    # Identity (directivas) survives the consolidation.
    contents = {it["content"] for it in await chica.items("inst_personal")}
    assert "responder conciso por defecto" in contents


# ── isolation + persistence ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_instance_isolation(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    await chica.remember("inst_a", "k", "secret_a")
    await chica.remember("inst_b", "k", "secret_b")

    assert await chica.size("inst_a") == 1
    assert await chica.size("inst_b") == 1
    a_contents = {it["content"] for it in await chica.items("inst_a")}
    assert a_contents == {"secret_a"}
    await chica.clear("inst_a")
    assert await chica.size("inst_a") == 0
    assert await chica.size("inst_b") == 1  # untouched


@pytest.mark.asyncio
async def test_chica_persists_across_reopen(tmp_path, monkeypatch):
    _, chica = await _init(tmp_path, monkeypatch)
    await chica.remember("inst_personal", "a", "alpha")
    # A fresh open of the same file (simulating a process restart) still sees it.
    assert await chica.size("inst_personal") == 1
    items = await chica.items("inst_personal")
    assert items[0]["content"] == "alpha"
