"""Tests for lhassa.core.artifacts; the append-only work-product record.

Offline + $0: a tmp brain DB (monkeypatched ``db.DB_PATH``), no model, no network.
Covers the register/get/list roundtrip, JSON survival of paths/metadata, newest-
first ordering, and a missing lookup returning ``None``.
"""

import pytest
import pytest_asyncio  # noqa: F401

from lhassa.core import artifacts, db


@pytest.mark.asyncio
async def test_register_get_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    artifact_id = await artifacts.register(
        "inst_personal",
        kind="cv",
        paths=["/tmp/cv_sezzle.tex", "/tmp/cv_sezzle.pdf"],
        metadata={"company": "sezzle", "role": "AI Engineer", "run_id": "r1"},
        origin_department="interns",
    )
    assert artifact_id.startswith("art_")

    row = await artifacts.get("inst_personal", artifact_id)
    assert row is not None
    assert row["kind"] == "cv"
    assert row["paths"] == ["/tmp/cv_sezzle.tex", "/tmp/cv_sezzle.pdf"]
    assert row["metadata"] == {"company": "sezzle", "role": "AI Engineer", "run_id": "r1"}
    assert row["origin_department"] == "interns"
    assert row["instance_id"] == "inst_personal"
    assert row["creado_en"]


@pytest.mark.asyncio
async def test_get_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    assert await artifacts.get("inst_personal", "art_does_not_exist") is None


@pytest.mark.asyncio
async def test_list_artifacts_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    first = await artifacts.register("inst_personal", kind="cv", paths=["/tmp/a.pdf"])
    second = await artifacts.register("inst_personal", kind="report", paths=["/tmp/b.pdf"])

    rows = await artifacts.list_artifacts("inst_personal", limit=50)
    ids = [r["id"] for r in rows]
    assert ids[0] == second
    assert ids[1] == first


@pytest.mark.asyncio
async def test_list_artifacts_scoped_to_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    await artifacts.register("inst_a", kind="cv", paths=["/tmp/a.pdf"])
    await artifacts.register("inst_b", kind="cv", paths=["/tmp/b.pdf"])

    rows_a = await artifacts.list_artifacts("inst_a")
    assert len(rows_a) == 1
    assert rows_a[0]["instance_id"] == "inst_a"


@pytest.mark.asyncio
async def test_register_defaults_metadata_and_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()

    artifact_id = await artifacts.register("inst_personal", kind="report", paths=["/tmp/r.pdf"])
    row = await artifacts.get("inst_personal", artifact_id)
    assert row["metadata"] == {}
    assert row["origin_department"] == ""
