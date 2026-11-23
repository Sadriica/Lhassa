"""Tests for the $0 semantic answer cache (lhassa/memory/answer_cache.py).

Uses the real (tmp-redirected) core DB via the autouse ``_isolate_brain_db``
conftest fixture and the deterministic offline HashingEmbedder; no network,
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
        assert hit is None  # expired; must not be served

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


def test_stats_reports_best_similarity_on_a_near_miss():
    """A near-miss (below threshold) still fills stats with the best similarity seen,
    so a caller can tell "we almost had it" apart from "nothing to compare against"."""

    async def _run():
        await db.init_db()
        cfg = _cfg()
        await answer_cache.store(
            INSTANCE, "What is the capital of France?", "Paris.", config=cfg
        )

        stats: dict = {}
        hit = await answer_cache.lookup(
            INSTANCE,
            "What is the capital city of France?",
            threshold=0.999,  # deliberately unreachable, forces a miss
            config=cfg,
            stats=stats,
        )
        assert hit is None
        assert stats["hit"] is False
        assert stats["scanned"] == 1
        assert stats["best_similarity"] > -1.0
        assert stats["best_similarity"] < 0.999

    asyncio.run(_run())


def test_stats_reports_hit_true_and_matches_return_value():
    async def _run():
        await db.init_db()
        cfg = _cfg()
        await answer_cache.store(
            INSTANCE, "What is the capital of France?", "Paris.", config=cfg
        )

        stats: dict = {}
        hit = await answer_cache.lookup(
            INSTANCE, "What is the capital of France?", threshold=0.90, config=cfg, stats=stats
        )
        assert hit is not None
        answer, sim = hit
        assert stats["hit"] is True
        assert stats["scanned"] == 1
        assert stats["best_similarity"] == sim

    asyncio.run(_run())


def test_stats_scanned_zero_and_best_similarity_default_on_empty_cache():
    async def _run():
        await db.init_db()
        cfg = _cfg()
        stats: dict = {}
        hit = await answer_cache.lookup(INSTANCE, "anything at all", config=cfg, stats=stats)
        assert hit is None
        assert stats == {"best_similarity": -1.0, "scanned": 0, "hit": False}

    asyncio.run(_run())


def test_stats_is_untouched_shape_when_not_passed_does_not_break_callers():
    """Existing callers that don't pass ``stats`` keep the exact same return value
    (return-type/signature backward compatibility)."""

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


def test_store_writes_the_embedder_stamp():
    async def _run():
        await db.init_db()
        cfg = _cfg(embedder_backend="hash")  # force the offline embedder, deterministic
        cid = await answer_cache.store(
            INSTANCE, "What is the capital of France?", "Paris.", config=cfg
        )
        rows = await db.list_answer_cache(INSTANCE)
        row = next(r for r in rows if r["id"] == cid)
        assert row["embedder"] == "hashing-v1"

    asyncio.run(_run())


def test_lookup_skips_a_row_stamped_with_a_different_embedder():
    """A row embedded by a different embedder must never be cosine-compared
    against the current query vector; mismatch is a MISS, never a garbage
    similarity score (mirrors memory.load._semantic_layer's own guard)."""

    async def _run():
        await db.init_db()
        cfg = _cfg()
        cid = await answer_cache.store(
            INSTANCE, "What is the capital of France?", "Paris.", config=cfg
        )
        async with __import__("lhassa._vendor.aiosqlite", fromlist=["connect"]).connect(
            db.DB_PATH
        ) as conn:
            await conn.execute(
                "UPDATE answer_cache SET embedder = ? WHERE id = ?",
                ("some-other-embedder", cid),
            )
            await conn.commit()

        hit = await answer_cache.lookup(
            INSTANCE, "What is the capital of France?", threshold=0.90, config=cfg
        )
        assert hit is None

    asyncio.run(_run())


def test_lookup_stats_report_a_scanned_row_that_mismatches_embedder():
    """The row is still 'scanned' (fresh, not TTL-expired) but never contributes
    a similarity score once its embedder doesn't match the current one."""

    async def _run():
        await db.init_db()
        cfg = _cfg()
        cid = await answer_cache.store(
            INSTANCE, "What is the capital of France?", "Paris.", config=cfg
        )
        async with __import__("lhassa._vendor.aiosqlite", fromlist=["connect"]).connect(
            db.DB_PATH
        ) as conn:
            await conn.execute(
                "UPDATE answer_cache SET embedder = ? WHERE id = ?",
                ("some-other-embedder", cid),
            )
            await conn.commit()

        stats: dict = {}
        hit = await answer_cache.lookup(
            INSTANCE, "What is the capital of France?", config=cfg, stats=stats
        )
        assert hit is None
        assert stats["hit"] is False
        assert stats["scanned"] == 1
        assert stats["best_similarity"] == -1.0  # never updated; no comparable row

    asyncio.run(_run())


def test_reindex_all_restamps_the_embedder_on_answer_cache_rows():
    from lhassa.memory import embeddings

    async def _run():
        await db.init_db()
        cfg = _cfg(embedder_backend="hash")  # force the offline embedder, deterministic
        cid = await answer_cache.store(
            INSTANCE, "What is the capital of France?", "Paris.", config=cfg
        )
        # Simulate a pre-migration/legacy row with a stale stamp.
        async with __import__("lhassa._vendor.aiosqlite", fromlist=["connect"]).connect(
            db.DB_PATH
        ) as conn:
            await conn.execute(
                "UPDATE answer_cache SET embedder = ? WHERE id = ?", ("stale-embedder", cid)
            )
            await conn.commit()

        summary = await embeddings.reindex_all(INSTANCE, config=cfg)
        assert summary["answers"] == 1

        rows = await db.list_answer_cache(INSTANCE)
        row = next(r for r in rows if r["id"] == cid)
        assert row["embedder"] == "hashing-v1"

    asyncio.run(_run())


def test_stats_default_on_lookup_failure(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("db is down")

    monkeypatch.setattr(db, "list_answer_cache", _boom)

    async def _run():
        stats: dict = {}
        hit = await answer_cache.lookup(INSTANCE, "what is the capital of France", config=_cfg(), stats=stats)
        assert hit is None
        # A failure before scanning still leaves stats at the initialized defaults.
        assert stats == {"best_similarity": -1.0, "scanned": 0, "hit": False}

    asyncio.run(_run())
