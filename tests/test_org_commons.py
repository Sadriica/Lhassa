"""Tests for lhassa.org.commons — the read-only cross-cutting source of truth."""

import pytest
import pytest_asyncio  # noqa: F401


async def _init(tmp_path, monkeypatch):
    import lhassa.core.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    await db.init_db()
    return db


@pytest.mark.asyncio
async def test_org_commons_table_created(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
    assert "org_commons" in tables


@pytest.mark.asyncio
async def test_general_writes_and_all_read(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org import commons

    await commons.set(
        "policy.refunds", {"max_usd": 100}, by_role="general", by_actor="owner",
        instance_id="inst_personal",
    )
    # readable by anyone — no role gate on reads
    val = await commons.get("policy.refunds", instance_id="inst_personal")
    assert val == {"max_usd": 100}


@pytest.mark.asyncio
async def test_department_write_denied(tmp_path, monkeypatch):
    await _init(tmp_path, monkeypatch)
    from lhassa.org import commons

    with pytest.raises(PermissionError):
        await commons.set(
            "policy.refunds", {"max_usd": 999999}, by_role="department",
            by_actor="sales_bot", instance_id="inst_personal",
        )
    # nothing persisted
    assert await commons.get("policy.refunds", instance_id="inst_personal") is None


@pytest.mark.asyncio
async def test_single_shared_row_no_per_department_copy(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.org import commons

    await commons.set("orgchart", ["general", "dept:sales"], by_role="general",
                      by_actor="owner", instance_id="inst_personal")
    await commons.set("orgchart", ["general", "dept:sales", "dept:eng"],
                      by_role="general", by_actor="owner", instance_id="inst_personal")

    # one curator overwrote the same shared row — exactly one row exists
    from lhassa._vendor import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM org_commons WHERE instance_id=? AND clave=?",
            ("inst_personal", "orgchart"),
        ) as cur:
            (count,) = await cur.fetchone()
    assert count == 1
    assert await commons.get("orgchart", instance_id="inst_personal") == [
        "general", "dept:sales", "dept:eng"
    ]


@pytest.mark.asyncio
async def test_get_all_and_curator_recorded(tmp_path, monkeypatch):
    db = await _init(tmp_path, monkeypatch)
    from lhassa.org import commons

    await commons.set("a", 1, by_role="general", by_actor="owner",
                      instance_id="inst_personal")
    await commons.set("b", "two", by_role="general", by_actor="owner",
                      instance_id="inst_personal")
    allv = await commons.get_all(instance_id="inst_personal")
    assert allv == {"a": 1, "b": "two"}

    row = await db.get_commons("inst_personal", "a")
    assert row["curator"] == "owner"
