"""Tests for the $0 semantic answer cache (lhassa/memory/answer_cache.py).

Uses the real (tmp-redirected) core DB via the autouse ``_isolate_brain_db``
conftest fixture and the deterministic offline HashingEmbedder — no network,
no model, no mocking needed for the embedder itself.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from lhassa.core import db
from lhassa.memory import answer_cache

INSTANCE = "inst_test"


def _cfg(**over):
    base = dict(answer_cache_threshold=0.90, answer_cache_ttl=7 * 24 * 3600.0)
    base.update(over)
    return SimpleNamespace(**base)


def test_store_then_lookup_roundtrip_hits_above_threshold():
    async def _run():
        await db.init_db()
        cfg = _cfg()
        cid = await answer_cache.store(
            INSTANCE, "What is the capital of France?", "Paris.", config=cfg
        )
        assert cid is not None

        hit = await answer_cache.lookup(
            INSTANCE, "What is the capital of France?", threshold=0.90, config=cfg
        )
        assert hit is not None
        answer, sim = hit
        assert answer == "Paris."
        assert sim >= 0.90

    asyncio.run(_run())


def test_lookup_miss_below_threshold_for_unrelated_question():
    async def _run():
        await db.init_db()
        cfg = _cfg()
        await answer_cache.store(
            INSTANCE, "What is the capital of France?", "Paris.", config=cfg
        )

        hit = await answer_cache.lookup(
            INSTANCE,
            "How do I bake a chocolate cake from scratch?",
            threshold=0.90,
            config=cfg,
        )
        assert hit is None

    asyncio.run(_run())


def test_ttl_expiry_ignores_stale_entries():
    async def _run():
        await db.init_db()
        cfg = _cfg(answer_cache_ttl=60.0)  # 1 minute TTL
        cid = await answer_cache.store(
            INSTANCE, "What is the capital of France?", "Paris.", config=cfg
        )
        assert cid is not None

        # Backdate the row's creado_en well past the TTL.
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        async with __import__("lhassa._vendor.aiosqlite", fromlist=["connect"]).connect(
            db.DB_PATH
        ) as conn:
            await conn.execute(
                "UPDATE answer_cache SET creado_en = ? WHERE id = ?", (stale_ts, cid)
            )
            await conn.commit()

        hit = await answer_cache.lookup(
            INSTANCE, "What is the capital of France?", threshold=0.90, config=cfg
        )
        assert hit is None  # expired — must not be served

    asyncio.run(_run())


def test_hit_bumps_the_hits_counter():
    async def _run():
        await db.init_db()
        cfg = _cfg()
        cid = await answer_cache.store(
            INSTANCE, "What is the capital of France?", "Paris.", config=cfg
        )

        hit = await answer_cache.lookup(
            INSTANCE, "What is the capital of France?", threshold=0.90, config=cfg
        )
        assert hit is not None

        rows = await db.list_answer_cache(INSTANCE)
        row = next(r for r in rows if r["id"] == cid)
        assert row["hits"] == 1

    asyncio.run(_run())


def test_store_never_raises_on_bad_input():
    async def _run():
        await db.init_db()
        cfg = _cfg()
        assert await answer_cache.store(INSTANCE, "", "answer", config=cfg) is None
        assert await answer_cache.store(INSTANCE, "question", "", config=cfg) is None

    asyncio.run(_run())


def test_lookup_never_raises_on_empty_cache():
    async def _run():
        await db.init_db()
        cfg = _cfg()
        hit = await answer_cache.lookup(INSTANCE, "anything at all", config=cfg)
        assert hit is None

    asyncio.run(_run())


def test_lookup_degrades_to_miss_on_db_failure(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("db is down")

    monkeypatch.setattr(db, "list_answer_cache", _boom)

    async def _run():
        hit = await answer_cache.lookup(INSTANCE, "what is the capital of France", config=_cfg())
        assert hit is None

    asyncio.run(_run())
