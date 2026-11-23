"""Tests for the embeddings-migration reindex path:
``lhassa.memory.embeddings.reindex_all`` / ``_reindex_answer_cache`` and the
``lhassa memory reindex`` CLI command.

Fully offline: the brain DB is redirected to a tmp file, and ``Config`` is
forced onto the offline hashing embedder (``embedder_backend = "hash"``) so no
real network call happens; determinism regardless of whether a real Ollama
happens to be running on the machine executing the tests.
"""

from __future__ import annotations

import asyncio
import json

from typer.testing import CliRunner

from lhassa.cli.app import app
from lhassa.config import Config

runner = CliRunner()

INSTANCE = Config.instance_id


def _use_tmp_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    return db


# ── lhassa.memory.embeddings.reindex_all / _reindex_answer_cache ─────────────────


def test_reindex_all_reembeds_facts_and_answer_cache(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)

    async def _run():
        from lhassa.memory import facts
        from lhassa.memory.embeddings import HashingEmbedder, reindex_all

        await facts.add_fact(INSTANCE, "dato", "uno")
        await facts.add_fact(INSTANCE, "dato", "dos")

        # Simulate two answer_cache rows written by a DIFFERENT (stale) embedder;
        # a mismatched dimension is the smoking gun a reindex must fix.
        await db.insert_answer_cache(
            {
                "instance_id": INSTANCE,
                "question": "what is the capital of france",
                "answer": "Paris.",
                "embedding": [0.1, 0.2, 0.3],  # not HashingEmbedder's 256-dim
            }
        )
        await db.insert_answer_cache(
            {
                "instance_id": INSTANCE,
                "question": "how do I undo a git commit",
                "answer": "git reset --soft HEAD~1",
                "embedding": [0.4, 0.5],
            }
        )

        class Cfg:
            embedder_backend = "hash"

        result = await reindex_all(INSTANCE, Cfg)

        assert result["facts"] == 2
        assert result["answers"] == 2
        assert result["answers_failed"] == 0
        assert result["embedder"] == "hash:v1"

        fact_rows = await db.get_fact_embeddings(INSTANCE)
        assert len(fact_rows) == 2
        assert all(r["model"] == "hashing-v1" for r in fact_rows)
        assert all(len(r["vector"]) == HashingEmbedder().dim for r in fact_rows)

        cache_rows = await db.list_answer_cache(INSTANCE)
        assert len(cache_rows) == 2
        assert all(len(r["embedding"]) == HashingEmbedder().dim for r in cache_rows)

    asyncio.run(_run())


def test_reindex_all_on_empty_instance_is_a_noop(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)

    async def _run():
        from lhassa.memory.embeddings import reindex_all

        class Cfg:
            embedder_backend = "hash"

        result = await reindex_all("inst_nothing", Cfg)
        assert result == {
            "facts": 0,
            "answers": 0,
            "answers_failed": 0,
            "embedder": "hash:v1",
        }

    asyncio.run(_run())


def test_reindex_answer_cache_is_best_effort_per_row(tmp_path, monkeypatch):
    """One row's embed() raising must not abort the whole batch."""
    db = _use_tmp_db(tmp_path, monkeypatch)

    async def _run():
        from lhassa.memory.embeddings import _reindex_answer_cache

        await db.insert_answer_cache(
            {
                "instance_id": INSTANCE,
                "question": "good question",
                "answer": "good answer",
                "embedding": [0.0],
            }
        )
        await db.insert_answer_cache(
            {
                "instance_id": INSTANCE,
                "question": "poison pill",
                "answer": "answer",
                "embedding": [0.0],
            }
        )

        class _FlakyEmbedder:
            name = "flaky"

            def embed(self, texts):
                if texts == ["poison pill"]:
                    raise RuntimeError("boom")
                return [[1.0, 2.0]]

        updated, failed = await _reindex_answer_cache(INSTANCE, _FlakyEmbedder())
        assert updated == 1
        assert failed == 1

    asyncio.run(_run())


# ── lhassa memory reindex (CLI) ───────────────────────────────────────────────────


def _force_hash_backend(monkeypatch):
    # ``reindex_cmd`` does ``from lhassa.config import Config`` INSIDE the
    # function (lazy, like the rest of the CLI), so it always reads whatever
    # class currently sits at ``lhassa.config.Config``: which some OTHER
    # test's ``importlib.reload(lhassa.config)`` may have swapped out from
    # under a module-level import. Patch through the live module binding so
    # this test stays correct regardless of collection/execution order.
    import lhassa.config as config_mod

    monkeypatch.setattr(config_mod.Config, "embedder_backend", "hash", raising=False)


def test_cli_memory_reindex_json(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    _force_hash_backend(monkeypatch)

    async def _seed():
        from lhassa.memory import facts

        await facts.add_fact(INSTANCE, "dato", "seeded fact one")
        await db.insert_answer_cache(
            {
                "instance_id": INSTANCE,
                "question": "seeded question",
                "answer": "seeded answer",
                "embedding": [0.0, 1.0],
            }
        )

    asyncio.run(_seed())

    res = runner.invoke(app, ["memory", "reindex", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["facts"] == 1
    assert payload["answers"] == 1
    assert payload["embedder"] == "hash:v1"


def test_cli_memory_reindex_plain_output(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    _force_hash_backend(monkeypatch)

    res = runner.invoke(app, ["memory", "reindex"])
    assert res.exit_code == 0, res.output
    assert "Reindexed with" in res.output
    assert "hash:v1" in res.output
    assert "facts" in res.output
    assert "answers" in res.output


def test_cli_memory_reindex_on_empty_db_never_crashes(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    _force_hash_backend(monkeypatch)

    res = runner.invoke(app, ["memory", "reindex", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["facts"] == 0
    assert payload["answers"] == 0
