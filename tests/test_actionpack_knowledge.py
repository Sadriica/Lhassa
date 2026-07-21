"""Tests for the ``ingest_knowledge`` action (lhassa/actionpack/_knowledge.py).

Offline, no network: a real tmp knowledge root + tmp brain DB, matching the
pattern in tests/test_derive_world_facts_action.py / test_calendar_sync_action.py.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.actionpack import _knowledge
from lhassa.core import db


@pytest.fixture
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    return db


def _cfg(**over):
    base = dict(
        instance_id="inst_personal",
        knowledge_roots="",
        knowledge_max_file_kb=512,
        knowledge_chunk_chars=2000,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_no_roots_configured_is_a_clean_noop(_db):
    out = asyncio.run(_knowledge.ingest_knowledge({"config": _cfg()}))
    assert out == "ingest_knowledge: no knowledge roots set"


def test_ingests_configured_root(_db, tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.md").write_text("A note about the ocean.")
    (root / ".env").write_text("SECRET=nope")

    out = asyncio.run(_knowledge.ingest_knowledge({"config": _cfg(knowledge_roots=str(root))}))
    assert "indexed 1 files" in out
    assert "skipped 1 secret-shaped" in out


def test_never_raises_on_bad_params():
    out = asyncio.run(_knowledge.ingest_knowledge("not a dict"))  # type: ignore[arg-type]
    assert "ingest_knowledge" in out


def test_never_raises_when_ingest_errors(_db, monkeypatch, tmp_path):
    from lhassa.memory import knowledge as knowledge_mod

    async def boom(instance_id, *, config=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(knowledge_mod, "ingest", boom)

    out = asyncio.run(
        _knowledge.ingest_knowledge({"config": _cfg(knowledge_roots=str(tmp_path))})
    )
    assert "error" in out.lower() or "runtimeerror" in out.lower()
