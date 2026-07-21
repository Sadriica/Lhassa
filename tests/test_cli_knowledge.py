"""Tests for ``lhassa knowledge ingest|search|clear`` (document perception CLI).

Fully offline: the brain DB is redirected to a tmp file, and ``Config``'s
class-level knowledge knobs are monkeypatched to point at a tmp root.
"""

from __future__ import annotations

import asyncio
import json

from typer.testing import CliRunner

from lhassa.cli.app import app

runner = CliRunner()


def _use_tmp_db(tmp_path, monkeypatch):
    import lhassa.core.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())
    return db


def _point_roots_at(tmp_path, monkeypatch, root):
    import lhassa.config as config_mod

    monkeypatch.setattr(config_mod.Config, "knowledge_roots", str(root))
    monkeypatch.setattr(config_mod.Config, "knowledge_max_file_kb", 512)
    monkeypatch.setattr(config_mod.Config, "knowledge_chunk_chars", 2000)


# ── ingest ────────────────────────────────────────────────────────────────────


def test_knowledge_ingest_no_roots_configured(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config_mod.Config, "knowledge_roots", "")

    res = runner.invoke(app, ["knowledge", "ingest", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["roots"] == 0
    assert "no knowledge roots set" in payload["message"]


def test_knowledge_ingest_indexes_files(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.md").write_text("A note about the mountains.")
    (root / ".env").write_text("SECRET=nope")
    _point_roots_at(tmp_path, monkeypatch, root)

    res = runner.invoke(app, ["knowledge", "ingest", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["files_indexed"] == 1
    assert payload["skipped_secrets"] >= 1


# ── search ────────────────────────────────────────────────────────────────────


def test_knowledge_search_finds_indexed_chunk(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.md").write_text("A detailed note about mountain hiking trails.")
    _point_roots_at(tmp_path, monkeypatch, root)

    ingest_res = runner.invoke(app, ["knowledge", "ingest", "--json"])
    assert ingest_res.exit_code == 0, ingest_res.output

    res = runner.invoke(app, ["knowledge", "search", "mountain hiking", "--json"])
    assert res.exit_code == 0, res.output
    hits = json.loads(res.output)
    assert hits
    assert hits[0]["source_path"].endswith("a.md")


def test_knowledge_search_empty_index(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    _use_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config_mod.Config, "knowledge_roots", "")

    res = runner.invoke(app, ["knowledge", "search", "anything", "--json"])
    assert res.exit_code == 0, res.output
    hits = json.loads(res.output)
    assert hits == []


# ── clear ─────────────────────────────────────────────────────────────────────


def test_knowledge_clear_wipes_index(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.md").write_text("Some content to be cleared.")
    _point_roots_at(tmp_path, monkeypatch, root)

    runner.invoke(app, ["knowledge", "ingest", "--json"])

    res = runner.invoke(app, ["knowledge", "clear", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["deleted"] >= 1

    res2 = runner.invoke(app, ["knowledge", "search", "cleared", "--json"])
    assert json.loads(res2.output) == []
