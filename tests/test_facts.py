"""Tests for lhassa.memory.facts — typed facts in the append-only grande."""

import pytest


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


# ── schema ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_facts_table_has_instance_id(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute("PRAGMA table_info(facts)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    expected = {
        "id", "instance_id", "tipo", "contenido", "pinned", "tags",
        "procedencia", "confianza", "creado_en", "actualizado_en",
    }
    assert expected.issubset(cols)
    assert "instance_id" in cols


# ── add / load ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_and_load_fact(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    f = await facts.add_fact(
        "inst_personal", "dato", "le gusta el té",
        tags=["gustos"], procedencia="owner", confianza=0.9,
    )
    assert f.id and f.tipo == "dato"
    loaded = await facts.load_facts("inst_personal")
    assert len(loaded) == 1
    got = loaded[0]
    assert got.contenido == "le gusta el té"
    assert got.tags == ["gustos"]
    assert got.confianza == 0.9
    assert got.procedencia == "owner"
    assert got.pinned is False


@pytest.mark.asyncio
async def test_add_fact_rejects_bad_tipo(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    with pytest.raises(ValueError):
        await facts.add_fact("inst_personal", "nope", "x")


@pytest.mark.asyncio
async def test_load_filters_tipo_and_pinned(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_personal", "dato", "a", pinned=False)
    await facts.add_fact("inst_personal", "dato", "b", pinned=True)
    await facts.add_fact("inst_personal", "directiva", "c", pinned=True)

    datos = await facts.load_facts("inst_personal", tipo="dato")
    assert {f.contenido for f in datos} == {"a", "b"}

    pinned = await facts.load_facts("inst_personal", pinned=True)
    assert {f.contenido for f in pinned} == {"b", "c"}

    directivas = await facts.load_facts("inst_personal", tipo="directiva")
    assert [f.contenido for f in directivas] == ["c"]


@pytest.mark.asyncio
async def test_load_by_tags(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_personal", "dato", "a", tags=["work", "proj"])
    await facts.add_fact("inst_personal", "dato", "b", tags=["home"])
    await facts.add_fact("inst_personal", "dato", "c", tags=["proj"])

    got = await facts.load_by_tags("inst_personal", ["proj"])
    assert {f.contenido for f in got} == {"a", "c"}

    assert await facts.load_by_tags("inst_personal", []) == []
    assert await facts.load_by_tags("inst_personal", ["missing"]) == []


@pytest.mark.asyncio
async def test_facts_isolated_by_instance(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_a", "dato", "secret_a")
    await facts.add_fact("inst_b", "dato", "secret_b")
    a = await facts.load_facts("inst_a")
    assert [f.contenido for f in a] == ["secret_a"]
    assert all(f.instance_id == "inst_a" for f in a)


@pytest.mark.asyncio
async def test_append_only_supersede_is_new_row(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_personal", "dato", "vive en Lima", confianza=0.5)
    await facts.add_fact("inst_personal", "dato", "vive en Lima", confianza=0.9)
    # Both rows persist — nothing was overwritten.
    rows = await facts.load_facts("inst_personal")
    assert len(rows) == 2


# ── contradiction resolution ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_picks_newest_and_highest_confidence(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    old = await facts.add_fact("inst_personal", "dato", "vive en Lima", confianza=0.6)
    new = await facts.add_fact("inst_personal", "dato", "vive en Lima", confianza=0.95)
    resolved = facts.resolve(await facts.load_facts("inst_personal"))
    assert len(resolved) == 1
    assert resolved[0].id == new.id
    assert resolved[0].confianza == 0.95
    assert old.id != new.id


@pytest.mark.asyncio
async def test_resolve_conflict_key_is_normalized_contenido(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    f1 = await facts.add_fact("inst_personal", "dato", "Vive en Lima")
    f2 = await facts.add_fact("inst_personal", "dato", "  vive en lima  ", confianza=1.0)
    resolved = facts.resolve(await facts.load_facts("inst_personal"))
    # Same normalized contenido → one winner (the newer one).
    assert len(resolved) == 1
    assert resolved[0].id == f2.id
    assert f1.id != f2.id


@pytest.mark.asyncio
async def test_resolve_keeps_distinct_facts(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.memory import facts
    await facts.add_fact("inst_personal", "dato", "a")
    await facts.add_fact("inst_personal", "dato", "b")
    resolved = facts.resolve(await facts.load_facts("inst_personal"))
    assert {f.contenido for f in resolved} == {"a", "b"}
