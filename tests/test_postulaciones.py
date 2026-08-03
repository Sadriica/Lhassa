"""Tests for the ``tracking_postulaciones`` typed task — offline, tmp brain DB.

Covers the DB helper round-trip (add/update/list) with instance isolation, the
deterministic action's add/update/list summaries, the never-raises contract, and
the optional/guarded MCP pull (injected as a stub — no real MCP).
"""

import pytest
import pytest_asyncio  # noqa: F401


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_brain.db")
    await db.init_db()
    return db


# ── schema ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_postulaciones_table_exists_with_instance_id(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute("PRAGMA table_info(postulaciones)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert "instance_id" in cols
    for c in ("empresa", "rol", "estado", "fuente", "link", "notas",
              "creado_en", "actualizado_en"):
        assert c in cols, f"missing column {c}"


# ── DB helpers round-trip ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_update_list_roundtrip(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    pid = await db.add_postulacion(
        "inst_personal", "Acme", "Backend Intern",
        estado="enviada", fuente="LinkedIn", link="http://x", notas="ref Ana",
    )
    rows = await db.list_postulaciones("inst_personal")
    assert len(rows) == 1
    assert rows[0]["empresa"] == "Acme"
    assert rows[0]["rol"] == "Backend Intern"
    assert rows[0]["estado"] == "enviada"

    ok = await db.update_postulacion_estado("inst_personal", pid, "entrevista")
    assert ok is True
    rows = await db.list_postulaciones("inst_personal", estado="entrevista")
    assert len(rows) == 1
    assert rows[0]["estado"] == "entrevista"
    # updated timestamp advanced (or at least is set)
    assert rows[0]["actualizado_en"]


@pytest.mark.asyncio
async def test_update_missing_returns_false(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    ok = await db.update_postulacion_estado("inst_personal", "post_nope", "oferta")
    assert ok is False


@pytest.mark.asyncio
async def test_instance_isolation(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    await db.add_postulacion("inst_a", "AlphaCo")
    await db.add_postulacion("inst_b", "BetaCo")
    rows_a = await db.list_postulaciones("inst_a")
    rows_b = await db.list_postulaciones("inst_b")
    assert [r["empresa"] for r in rows_a] == ["AlphaCo"]
    assert [r["empresa"] for r in rows_b] == ["BetaCo"]


# ── the deterministic action ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_action_add_then_list(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.core import actions

    out = await actions.tracking_postulaciones(
        {"op": "add", "instance_id": "inst_personal", "empresa": "Acme", "rol": "SRE"}
    )
    assert "Acme" in out and "añadida" in out

    listed = await actions.tracking_postulaciones(
        {"op": "list", "instance_id": "inst_personal"}
    )
    assert "1 postulaciones" in listed
    assert "Acme" in listed


@pytest.mark.asyncio
async def test_action_update_via_run_dispatch(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.core import actions

    pid = await db.add_postulacion("inst_personal", "Acme", estado="enviada")
    # dispatch through the async-aware registry runner
    out = await actions.run(
        "tracking_postulaciones",
        {"op": "update", "instance_id": "inst_personal", "id": pid, "estado": "oferta"},
    )
    assert "oferta" in out
    rows = await db.list_postulaciones("inst_personal")
    assert rows[0]["estado"] == "oferta"


@pytest.mark.asyncio
async def test_action_add_requires_empresa(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.core import actions
    out = await actions.tracking_postulaciones({"op": "add", "instance_id": "inst_personal"})
    assert "empresa" in out.lower()


@pytest.mark.asyncio
async def test_action_never_raises_on_garbage(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.core import actions
    # non-dict params and an unknown op must both produce a string, not raise
    out = await actions.tracking_postulaciones("not a dict")  # type: ignore[arg-type]
    assert isinstance(out, str)
    out2 = await actions.tracking_postulaciones({"op": "frobnicate", "instance_id": "i"})
    assert isinstance(out2, str)


# ── optional / guarded MCP pull (stubbed) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_action_mcp_pull_injected_stub(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.core import actions

    async def fake_pull():
        return [
            {"empresa": "FromEmail", "rol": "Intern", "estado": "enviada"},
            {"empresa": "FromSheet"},
            {"bogus": "no empresa -> skipped"},
        ]

    out = await actions.tracking_postulaciones(
        {"op": "list", "instance_id": "inst_personal", "mcp_pull": fake_pull}
    )
    assert "+2 de MCP" in out
    from lhassa.core import db
    rows = await db.list_postulaciones("inst_personal")
    assert {r["empresa"] for r in rows} == {"FromEmail", "FromSheet"}


@pytest.mark.asyncio
async def test_action_mcp_pull_failure_is_guarded(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.core import actions

    def boom():
        raise RuntimeError("source unreachable")

    # A failing pull must NOT break the deterministic core.
    out = await actions.tracking_postulaciones(
        {"op": "list", "instance_id": "inst_personal", "mcp_pull": boom}
    )
    assert isinstance(out, str)
    assert "de MCP" not in out  # nothing pulled
