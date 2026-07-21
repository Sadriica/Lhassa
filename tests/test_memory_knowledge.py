"""Tests for document perception (lhassa/memory/knowledge.py).

Offline, $0, no network, no model: the deterministic HashingEmbedder, tmp
knowledge roots, and the tmp-redirected core DB (autouse ``_isolate_brain_db``
conftest fixture). Covers the hard ignore-list (the CRITICAL privacy
guarantee: a secret-shaped file under a root is NEVER indexed), incremental
re-ingest, search, the size cap, and empty-roots behavior.
"""

import os
import time
from types import SimpleNamespace

from lhassa.core import db
from lhassa.memory import knowledge

INSTANCE = "inst_test"


def _cfg(roots, **over):
    base = dict(
        instance_id=INSTANCE,
        knowledge_roots=roots,
        knowledge_max_file_kb=512,
        knowledge_chunk_chars=2000,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# ── ingest: happy path + the hard ignore-list ────────────────────────────────


def test_ingest_indexes_text_files_and_skips_secrets(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "notes.md").write_text("# Trip notes\nThe elephant sanctuary was amazing.")
    (root / "readme.txt").write_text("Just a plain text readme about rockets.")
    (root / ".env").write_text("API_KEY=should-never-be-indexed")
    (root / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfakefake\n")

    cfg = _cfg(str(root))
    summary = _run(knowledge.ingest(INSTANCE, config=cfg))

    assert summary["roots"] == 1
    assert summary["files_indexed"] == 2
    assert summary["chunks"] >= 2
    assert summary["skipped_secrets"] >= 2

    rows = _run(db.list_knowledge_chunks(INSTANCE))
    all_text = " ".join(r["text"] for r in rows)
    all_paths = {r["source_path"] for r in rows}
    assert "elephant" in all_text
    assert "rockets" in all_text
    # CRITICAL: the secret-shaped files' content is NEVER indexed.
    assert "should-never-be-indexed" not in all_text
    assert "BEGIN OPENSSH PRIVATE KEY" not in all_text
    assert not any(p.endswith(".env") for p in all_paths)
    assert not any(p.endswith("id_rsa") for p in all_paths)


def test_ignore_list_blocks_dotfile_and_node_modules(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".hidden.md").write_text("secret-shaped dotfile content, never index me")
    nm = root / "node_modules"
    nm.mkdir()
    (nm / "pkg.md").write_text("node_modules content must never be indexed")
    (root / "real.md").write_text("Legitimate project note about the roadmap.")

    cfg = _cfg(str(root))
    summary = _run(knowledge.ingest(INSTANCE, config=cfg))

    assert summary["files_indexed"] == 1
    rows = _run(db.list_knowledge_chunks(INSTANCE))
    all_text = " ".join(r["text"] for r in rows)
    assert "roadmap" in all_text
    assert "never index me" not in all_text
    assert "must never be indexed" not in all_text


def test_ingest_pdf_via_monkeypatched_extractor(tmp_path, monkeypatch):
    root = tmp_path / "papers"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"%PDF-1.4 not a real pdf but has the magic bytes")

    monkeypatch.setattr(
        knowledge,
        "extract_text",
        lambda data, filename="": "quantum entanglement study results",
    )

    cfg = _cfg(str(root))
    summary = _run(knowledge.ingest(INSTANCE, config=cfg))

    assert summary["files_indexed"] == 1
    rows = _run(db.list_knowledge_chunks(INSTANCE))
    assert any("quantum entanglement" in r["text"] for r in rows)


def test_ingest_skips_file_over_size_cap(tmp_path):
    root = tmp_path / "big"
    root.mkdir()
    (root / "huge.md").write_text("x" * 5000)  # ~5KB

    cfg = _cfg(str(root), knowledge_max_file_kb=1)  # 1KB cap
    summary = _run(knowledge.ingest(INSTANCE, config=cfg))

    assert summary["files_indexed"] == 0
    assert summary["chunks"] == 0


def test_ingest_empty_roots_returns_nothing():
    cfg = _cfg("")
    summary = _run(knowledge.ingest(INSTANCE, config=cfg))
    assert summary == {
        "roots": 0,
        "files_scanned": 0,
        "files_indexed": 0,
        "chunks": 0,
        "skipped_secrets": 0,
    }


# ── incremental re-ingest ─────────────────────────────────────────────────────


def test_incremental_reingest_skips_unchanged_and_reindexes_touched(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    f = root / "log.md"
    f.write_text("First version of the log entry.")

    cfg = _cfg(str(root))
    first = _run(knowledge.ingest(INSTANCE, config=cfg))
    assert first["files_indexed"] == 1

    # Re-ingest unchanged — nothing new.
    second = _run(knowledge.ingest(INSTANCE, config=cfg))
    assert second["files_indexed"] == 0
    assert second["chunks"] == 0

    rows_before = _run(db.list_knowledge_chunks(INSTANCE))
    assert any("First version" in r["text"] for r in rows_before)

    # Touch the file (new content + a bumped mtime) — re-indexed.
    f.write_text("Second, updated version of the log entry.")
    new_mtime = time.time() + 5
    os.utime(f, (new_mtime, new_mtime))

    third = _run(knowledge.ingest(INSTANCE, config=cfg))
    assert third["files_indexed"] == 1

    rows_after = _run(db.list_knowledge_chunks(INSTANCE))
    all_text = " ".join(r["text"] for r in rows_after)
    assert "Second, updated version" in all_text
    assert "First version" not in all_text  # old chunks were deleted


# ── search ────────────────────────────────────────────────────────────────────


def test_search_returns_relevant_chunk_by_query(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "animals.md").write_text("The African elephant is the largest land mammal.")
    (root / "space.md").write_text("Rocket engines burn liquid oxygen and kerosene.")

    cfg = _cfg(str(root))
    _run(knowledge.ingest(INSTANCE, config=cfg))

    hits = _run(knowledge.search(INSTANCE, "elephant land mammal", config=cfg))
    assert hits
    assert hits[0]["source_path"].endswith("animals.md")
    assert "elephant" in hits[0]["text"].lower()
    assert hits[0]["score"] > 0.0


def test_search_no_match_returns_empty(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "animals.md").write_text("The African elephant is the largest land mammal.")

    cfg = _cfg(str(root))
    _run(knowledge.ingest(INSTANCE, config=cfg))

    hits = _run(knowledge.search(INSTANCE, "", config=cfg))
    assert hits == []


# ── clear ─────────────────────────────────────────────────────────────────────


def test_clear_wipes_the_index(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.md").write_text("Some note content.")

    cfg = _cfg(str(root))
    _run(knowledge.ingest(INSTANCE, config=cfg))
    assert _run(db.list_knowledge_chunks(INSTANCE))

    deleted = _run(knowledge.clear(INSTANCE, config=cfg))
    assert deleted >= 1
    assert _run(db.list_knowledge_chunks(INSTANCE)) == []


# ── defensive: never raises ──────────────────────────────────────────────────


def test_ingest_never_raises_on_bad_root():
    cfg = _cfg("/definitely/does/not/exist/anywhere")
    summary = _run(knowledge.ingest(INSTANCE, config=cfg))
    assert summary["roots"] == 0
    assert summary["files_indexed"] == 0


def test_search_degrades_to_empty_on_db_failure(monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(db, "list_knowledge_chunks", _boom)
    hits = _run(knowledge.search(INSTANCE, "anything", config=_cfg("")))
    assert hits == []
